// Command hcl-go-adapter connects the test runner to github.com/hashicorp/hcl/v2,
// the reference implementation of HCL. It is also useful on its own: run it on
// any file to see how the reference implementation reads it.
//
//	hcl-go-adapter capabilities
//	hcl-go-adapter parse <file.hcl>
//	hcl-go-adapter eval <file.hcl> [<variables.json>]
//
// The output formats are described in docs/protocol.md.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"os"
	"runtime/debug"
	"sort"

	"github.com/hashicorp/hcl/v2"
	"github.com/hashicorp/hcl/v2/hclsyntax"
	"github.com/zclconf/go-cty/cty"
	"github.com/zclconf/go-cty/cty/function"
	ctyjson "github.com/zclconf/go-cty/cty/json"
)

type object = map[string]any

const usage = `usage:
  hcl-go-adapter capabilities
  hcl-go-adapter parse <file.hcl>
  hcl-go-adapter eval <file.hcl> [<variables.json>]`

// unsupported is panicked when the input contains something the protocol has
// no representation for yet. run recovers it and reports an adapter error.
type unsupported struct{ what string }

func main() {
	out, err := run(os.Args[1:])
	if err == nil {
		enc := json.NewEncoder(os.Stdout)
		enc.SetEscapeHTML(false)
		err = enc.Encode(out)
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "hcl-go-adapter: %s\n", err)
		os.Exit(1)
	}
}

func run(args []string) (out any, err error) {
	defer func() {
		if r := recover(); r != nil {
			u, ok := r.(unsupported)
			if !ok {
				panic(r)
			}
			err = fmt.Errorf("unsupported: %s", u.what)
		}
	}()

	switch {
	case len(args) == 1 && args[0] == "capabilities":
		return capabilities(), nil
	case len(args) == 2 && args[0] == "parse":
		return parse(args[1])
	case len(args) == 2 && args[0] == "eval":
		return eval(args[1], "")
	case len(args) == 3 && args[0] == "eval":
		return eval(args[1], args[2])
	}
	return nil, errors.New(usage)
}

func capabilities() object {
	version := "unknown"
	if info, ok := debug.ReadBuildInfo(); ok {
		for _, dep := range info.Deps {
			if dep.Path == "github.com/hashicorp/hcl/v2" {
				version = dep.Version
			}
		}
	}
	return object{
		"implementation": "hashicorp/hcl",
		"version":        version,
		"operations":     []string{"parse", "eval"},
		"features":       []string{"unknown-values"},
	}
}

func parse(path string) (any, error) {
	src, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	file, diags := hclsyntax.ParseConfig(src, path, hcl.InitialPos)
	if diags.HasErrors() {
		return invalid("parse", diags), nil
	}
	c := converter{src: src}
	return object{"valid": true, "body": c.body(file.Body.(*hclsyntax.Body))}, nil
}

func eval(path, varsPath string) (any, error) {
	src, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	vars := map[string]cty.Value{}
	if varsPath != "" {
		if vars, err = readVariables(varsPath); err != nil {
			return nil, err
		}
	}
	file, diags := hclsyntax.ParseConfig(src, path, hcl.InitialPos)
	if diags.HasErrors() {
		return invalid("parse", diags), nil
	}
	ctx := &hcl.EvalContext{Variables: vars, Functions: map[string]function.Function{}}
	body, diags := evalBody(file.Body.(*hclsyntax.Body), ctx)
	if diags.HasErrors() {
		return invalid("eval", diags), nil
	}
	return object{"valid": true, "body": body}, nil
}

func evalBody(body *hclsyntax.Body, ctx *hcl.EvalContext) (object, hcl.Diagnostics) {
	var diags hcl.Diagnostics
	names := make([]string, 0, len(body.Attributes))
	for name := range body.Attributes {
		names = append(names, name)
	}
	sort.Strings(names) // keeps the order of reported errors stable

	attrs := object{}
	for _, name := range names {
		val, valDiags := body.Attributes[name].Expr.Value(ctx)
		diags = append(diags, valDiags...)
		attrs[name] = encodeValue(val)
	}
	blocks := []any{}
	for _, block := range body.Blocks {
		inner, blockDiags := evalBody(block.Body, ctx)
		diags = append(diags, blockDiags...)
		blocks = append(blocks, blockObject(block, inner))
	}
	return object{"attributes": attrs, "blocks": blocks}, diags
}

func blockObject(block *hclsyntax.Block, body object) object {
	labels := block.Labels
	if labels == nil {
		labels = []string{}
	}
	return object{"type": block.Type, "labels": labels, "body": body}
}

func invalid(phase string, diags hcl.Diagnostics) object {
	errs := []any{}
	for _, diag := range diags {
		if diag.Severity != hcl.DiagError {
			continue
		}
		e := object{"message": diag.Summary}
		if diag.Detail != "" {
			e["message"] = diag.Summary + ": " + diag.Detail
		}
		if diag.Subject != nil {
			e["range"] = encodeRange(*diag.Subject)
		}
		errs = append(errs, e)
	}
	return object{"valid": false, "phase": phase, "errors": errs}
}

func encodeRange(rng hcl.Range) object {
	pos := func(p hcl.Pos) object {
		return object{"line": p.Line, "column": p.Column, "byte": p.Byte}
	}
	return object{"start": pos(rng.Start), "end": pos(rng.End)}
}

// converter turns hclsyntax expressions into the protocol's expression trees.
type converter struct {
	src []byte
}

func (c *converter) body(body *hclsyntax.Body) object {
	attrs := object{}
	for name, attr := range body.Attributes {
		attrs[name] = c.expr(attr.Expr)
	}
	blocks := []any{}
	for _, block := range body.Blocks {
		blocks = append(blocks, blockObject(block, c.body(block.Body)))
	}
	return object{"attributes": attrs, "blocks": blocks}
}

var operators = map[*hclsyntax.Operation]string{
	hclsyntax.OpLogicalOr:          "||",
	hclsyntax.OpLogicalAnd:         "&&",
	hclsyntax.OpLogicalNot:         "!",
	hclsyntax.OpEqual:              "==",
	hclsyntax.OpNotEqual:           "!=",
	hclsyntax.OpGreaterThan:        ">",
	hclsyntax.OpGreaterThanOrEqual: ">=",
	hclsyntax.OpLessThan:           "<",
	hclsyntax.OpLessThanOrEqual:    "<=",
	hclsyntax.OpAdd:                "+",
	hclsyntax.OpSubtract:           "-",
	hclsyntax.OpMultiply:           "*",
	hclsyntax.OpDivide:             "/",
	hclsyntax.OpModulo:             "%",
	hclsyntax.OpNegate:             "-",
}

func (c *converter) expr(e hclsyntax.Expression) object {
	switch e := e.(type) {
	case *hclsyntax.LiteralValueExpr:
		return literal(e.Val)
	case *hclsyntax.TemplateExpr:
		return object{"kind": "template", "parts": c.templateParts(e.Parts)}
	case *hclsyntax.TemplateWrapExpr:
		part := object{"kind": "interpolation", "expr": c.expr(e.Wrapped)}
		return object{"kind": "template", "parts": []any{part}}
	case *hclsyntax.ScopeTraversalExpr:
		return traversal(nil, e.Traversal)
	case *hclsyntax.RelativeTraversalExpr:
		return traversal(c.expr(e.Source), e.Traversal)
	case *hclsyntax.ParenthesesExpr:
		return c.expr(e.Expression)
	case *hclsyntax.FunctionCallExpr:
		return object{"kind": "call", "name": e.Name, "args": c.exprs(e.Args), "expand_final": e.ExpandFinal}
	case *hclsyntax.ConditionalExpr:
		return object{
			"kind":         "conditional",
			"condition":    c.expr(e.Condition),
			"true_result":  c.expr(e.TrueResult),
			"false_result": c.expr(e.FalseResult),
		}
	case *hclsyntax.BinaryOpExpr:
		return object{"kind": "binary", "operator": operators[e.Op], "left": c.expr(e.LHS), "right": c.expr(e.RHS)}
	case *hclsyntax.UnaryOpExpr:
		return object{"kind": "unary", "operator": operators[e.Op], "operand": c.expr(e.Val)}
	case *hclsyntax.IndexExpr:
		return object{"kind": "index", "collection": c.expr(e.Collection), "key": c.expr(e.Key)}
	case *hclsyntax.TupleConsExpr:
		return object{"kind": "tuple", "elements": c.exprs(e.Exprs)}
	case *hclsyntax.ObjectConsExpr:
		items := []any{}
		for _, item := range e.Items {
			items = append(items, object{"key": c.objectKey(item.KeyExpr), "value": c.expr(item.ValueExpr)})
		}
		return object{"kind": "object", "items": items}
	case *hclsyntax.ForExpr:
		result := "tuple"
		if e.KeyExpr != nil {
			result = "object"
		}
		return object{
			"kind":       "for",
			"result":     result,
			"key_var":    optionalName(e.KeyVar),
			"value_var":  e.ValVar,
			"collection": c.expr(e.CollExpr),
			"key":        c.optionalExpr(e.KeyExpr),
			"value":      c.expr(e.ValExpr),
			"condition":  c.optionalExpr(e.CondExpr),
			"grouping":   e.Group,
		}
	case *hclsyntax.SplatExpr:
		return object{"kind": "splat", "source": c.expr(e.Source), "each": c.expr(e.Each)}
	case *hclsyntax.AnonSymbolExpr:
		return object{"kind": "splat_item"}
	}
	panic(unsupported{fmt.Sprintf("expression type %T", e)})
}

func (c *converter) exprs(exprs []hclsyntax.Expression) []any {
	out := []any{}
	for _, e := range exprs {
		out = append(out, c.expr(e))
	}
	return out
}

func (c *converter) optionalExpr(e hclsyntax.Expression) any {
	if e == nil {
		return nil
	}
	return c.expr(e)
}

func optionalName(name string) any {
	if name == "" {
		return nil
	}
	return name
}

// objectKey applies the rule that a bare identifier used as an object key is a
// literal name, while a parenthesized one is a variable reference.
func (c *converter) objectKey(e hclsyntax.Expression) object {
	key, ok := e.(*hclsyntax.ObjectConsKeyExpr)
	if !ok {
		return c.expr(e)
	}
	if name := hcl.ExprAsKeyword(key.Wrapped); name != "" && !key.ForceNonLiteral {
		return literal(cty.StringVal(name))
	}
	return c.expr(key.Wrapped)
}

func (c *converter) templateParts(parts []hclsyntax.Expression) []any {
	out := []any{}
	for _, part := range parts {
		out = append(out, c.templatePart(part))
	}
	return out
}

func (c *converter) templatePart(part hclsyntax.Expression) object {
	switch part := part.(type) {
	case *hclsyntax.LiteralValueExpr:
		// Literal text. Something like ${5} is also a literal, but never a
		// string, because a quoted string inside an interpolation is a template.
		if part.Val.Type() == cty.String {
			return literal(part.Val)
		}
	case *hclsyntax.ConditionalExpr:
		// The parser builds the same node for %{if} as for ${a ? b : c}, so
		// the source text is the only way to tell them apart.
		if bytes.HasPrefix(c.src[part.SrcRange.Start.Byte:], []byte("%{")) {
			return object{
				"kind":      "template_if",
				"condition": c.expr(part.Condition),
				"then":      c.templateParts(part.TrueResult.(*hclsyntax.TemplateExpr).Parts),
				"else":      c.templateParts(part.FalseResult.(*hclsyntax.TemplateExpr).Parts),
			}
		}
	case *hclsyntax.TemplateJoinExpr:
		// Only %{for} directives produce this node.
		loop := part.Tuple.(*hclsyntax.ForExpr)
		return object{
			"kind":       "template_for",
			"key_var":    optionalName(loop.KeyVar),
			"value_var":  loop.ValVar,
			"collection": c.expr(loop.CollExpr),
			"body":       c.templateParts(loop.ValExpr.(*hclsyntax.TemplateExpr).Parts),
		}
	}
	return object{"kind": "interpolation", "expr": c.expr(part)}
}

func traversal(source object, steps hcl.Traversal) object {
	node := source
	for _, step := range steps {
		switch step := step.(type) {
		case hcl.TraverseRoot:
			node = object{"kind": "variable", "name": step.Name}
		case hcl.TraverseAttr:
			node = object{"kind": "get_attr", "object": node, "name": step.Name}
		case hcl.TraverseIndex:
			node = object{"kind": "index", "collection": node, "key": literal(step.Key)}
		default:
			panic(unsupported{fmt.Sprintf("traversal step %T", step)})
		}
	}
	return node
}

func literal(v cty.Value) object {
	return object{"kind": "literal", "value": encodeValue(v)}
}

func encodeValue(v cty.Value) object {
	v, _ = v.UnmarkDeep()
	ty := v.Type()
	switch {
	case !v.IsKnown():
		return object{"unknown": typeJSON(ty)}
	case v.IsNull():
		return object{"null": typeJSON(ty)}
	case ty == cty.String:
		return object{"string": v.AsString()}
	case ty == cty.Number:
		return object{"number": formatNumber(v.AsBigFloat())}
	case ty == cty.Bool:
		return object{"bool": v.True()}
	case ty.IsTupleType():
		return object{"tuple": encodeElements(v)}
	case ty.IsObjectType():
		return object{"object": encodeAttributes(v)}
	case ty.IsListType():
		return object{"list": encodeElements(v), "element_type": typeJSON(ty.ElementType())}
	case ty.IsSetType():
		return object{"set": encodeElements(v), "element_type": typeJSON(ty.ElementType())}
	case ty.IsMapType():
		return object{"map": encodeAttributes(v), "element_type": typeJSON(ty.ElementType())}
	}
	panic(unsupported{"value of type " + ty.FriendlyName()})
}

func encodeElements(v cty.Value) []any {
	out := []any{}
	for it := v.ElementIterator(); it.Next(); {
		_, elem := it.Element()
		out = append(out, encodeValue(elem))
	}
	return out
}

func encodeAttributes(v cty.Value) object {
	out := object{}
	for it := v.ElementIterator(); it.Next(); {
		key, elem := it.Element()
		out[key.AsString()] = encodeValue(elem)
	}
	return out
}

func formatNumber(f *big.Float) string {
	switch {
	case f.IsInf() && f.Sign() > 0:
		return "Infinity"
	case f.IsInf():
		return "-Infinity"
	case f.Sign() == 0:
		return "0" // also negative zero
	}
	return f.Text('f', -1)
}

func typeJSON(ty cty.Type) json.RawMessage {
	raw, err := ctyjson.MarshalType(ty)
	if err != nil {
		panic(unsupported{"type " + ty.FriendlyName()})
	}
	return raw
}

func readVariables(path string) (map[string]cty.Value, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	vars := make(map[string]cty.Value, len(raw))
	for name, r := range raw {
		if vars[name], err = decodeValue(r); err != nil {
			return nil, fmt.Errorf("%s: variable %q: %w", path, name, err)
		}
	}
	return vars, nil
}

func decodeValue(data json.RawMessage) (cty.Value, error) {
	var node map[string]json.RawMessage
	if err := json.Unmarshal(data, &node); err != nil {
		return cty.NilVal, err
	}
	for kind, raw := range node {
		switch kind {
		case "string":
			var s string
			err := json.Unmarshal(raw, &s)
			return cty.StringVal(s), err
		case "number":
			var s string
			if err := json.Unmarshal(raw, &s); err != nil {
				return cty.NilVal, err
			}
			return cty.ParseNumberVal(s)
		case "bool":
			var b bool
			err := json.Unmarshal(raw, &b)
			return cty.BoolVal(b), err
		case "null", "unknown":
			ty, err := ctyjson.UnmarshalType(raw)
			if err != nil {
				return cty.NilVal, err
			}
			if kind == "null" {
				return cty.NullVal(ty), nil
			}
			return cty.UnknownVal(ty), nil
		case "tuple", "list", "set":
			var items []json.RawMessage
			if err := json.Unmarshal(raw, &items); err != nil {
				return cty.NilVal, err
			}
			elems := make([]cty.Value, len(items))
			for i, item := range items {
				var err error
				if elems[i], err = decodeValue(item); err != nil {
					return cty.NilVal, err
				}
			}
			return sequence(kind, node["element_type"], elems)
		case "object", "map":
			var items map[string]json.RawMessage
			if err := json.Unmarshal(raw, &items); err != nil {
				return cty.NilVal, err
			}
			attrs := make(map[string]cty.Value, len(items))
			for key, item := range items {
				var err error
				if attrs[key], err = decodeValue(item); err != nil {
					return cty.NilVal, err
				}
			}
			return mapping(kind, node["element_type"], attrs)
		}
	}
	return cty.NilVal, fmt.Errorf("not a value: %s", data)
}

func sequence(kind string, rawType json.RawMessage, elems []cty.Value) (v cty.Value, err error) {
	if kind == "tuple" {
		return cty.TupleVal(elems), nil
	}
	ety, err := ctyjson.UnmarshalType(rawType)
	if err != nil {
		return cty.NilVal, fmt.Errorf("%s element_type: %w", kind, err)
	}
	defer recoverInvalid(kind, &err) // cty panics if elements don't match
	switch {
	case kind == "list" && len(elems) == 0:
		return cty.ListValEmpty(ety), nil
	case kind == "list":
		return cty.ListVal(elems), nil
	case len(elems) == 0:
		return cty.SetValEmpty(ety), nil
	}
	return cty.SetVal(elems), nil
}

func mapping(kind string, rawType json.RawMessage, attrs map[string]cty.Value) (v cty.Value, err error) {
	if kind == "object" {
		return cty.ObjectVal(attrs), nil
	}
	ety, err := ctyjson.UnmarshalType(rawType)
	if err != nil {
		return cty.NilVal, fmt.Errorf("map element_type: %w", err)
	}
	defer recoverInvalid(kind, &err)
	if len(attrs) == 0 {
		return cty.MapValEmpty(ety), nil
	}
	return cty.MapVal(attrs), nil
}

func recoverInvalid(kind string, err *error) {
	if r := recover(); r != nil {
		*err = fmt.Errorf("invalid %s: %v", kind, r)
	}
}
