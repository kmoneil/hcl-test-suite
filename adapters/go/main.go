// Command hcl-go-adapter connects the test runner to github.com/hashicorp/hcl/v2,
// the reference implementation of HCL. It is also useful on its own: run it on
// any file to see how the reference implementation reads it.
//
//	hcl-go-adapter capabilities
//	hcl-go-adapter parse <file.hcl>
//	hcl-go-adapter eval <file.hcl> [<context.json>]
//	hcl-go-adapter decode <file.hcl or file.hcl.json> <context.json>
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
	"reflect"
	"runtime/debug"
	"sort"
	"strings"

	"github.com/hashicorp/hcl/v2"
	"github.com/hashicorp/hcl/v2/ext/customdecode"
	"github.com/hashicorp/hcl/v2/ext/typeexpr"
	"github.com/hashicorp/hcl/v2/hclsyntax"
	hcljson "github.com/hashicorp/hcl/v2/json"
	"github.com/zclconf/go-cty/cty"
	"github.com/zclconf/go-cty/cty/convert"
	"github.com/zclconf/go-cty/cty/function"
	ctyjson "github.com/zclconf/go-cty/cty/json"
)

type object = map[string]any

const usage = `usage:
  hcl-go-adapter capabilities
  hcl-go-adapter parse <file.hcl>
  hcl-go-adapter eval <file.hcl> [<context.json>]
  hcl-go-adapter decode <file.hcl or file.hcl.json> <context.json>`

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
	case len(args) == 3 && args[0] == "decode":
		return decode(args[1], args[2])
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
		"operations":     []string{"parse", "eval", "decode"},
		"features":       []string{"typed-values", "unknown-values", "functions", "json-syntax", "static-analysis", "type-expressions"},
	}
}

func parse(path string) (any, error) {
	src, err := readNativeFile(path)
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

// readNativeFile reads a file for parse or eval, which only take the native
// syntax: the JSON syntax can't be read without a schema.
func readNativeFile(path string) ([]byte, error) {
	if isJSONSyntax(path) {
		return nil, errors.New("JSON syntax files can only be decoded with a schema")
	}
	return os.ReadFile(path)
}

func isJSONSyntax(path string) bool {
	return strings.HasSuffix(path, ".hcl.json")
}

func eval(path, contextPath string) (any, error) {
	src, err := readNativeFile(path)
	if err != nil {
		return nil, err
	}
	settings := evalSettings{ctx: emptyEvalContext()}
	if contextPath != "" {
		if settings, err = readContext(contextPath); err != nil {
			return nil, err
		}
	}
	if settings.schema != nil {
		return nil, errors.New("eval doesn't take a schema; use decode")
	}
	file, diags := hclsyntax.ParseConfig(src, path, hcl.InitialPos)
	if diags.HasErrors() {
		return invalid("parse", diags), nil
	}
	body, diags := evalBody(file.Body.(*hclsyntax.Body), settings.ctx)
	if diags.HasErrors() {
		return invalid("eval", diags), nil
	}
	return object{"valid": true, "body": body}, nil
}

// decode applies a schema to a file's body, then the static analyses the schema
// asks for, then evaluates the attributes the schema selected. Each step covers
// the whole body, including nested blocks, before the next one starts, so that
// an input with errors in several phases reports the earliest phase.
func decode(path, contextPath string) (any, error) {
	src, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	settings, err := readContext(contextPath)
	if err != nil {
		return nil, err
	}
	if settings.schema == nil {
		return nil, errors.New("decode needs a schema")
	}
	schema, err := newBodySchema(settings.schema)
	if err != nil {
		return nil, fmt.Errorf("%s: schema: %w", contextPath, err)
	}
	var file *hcl.File
	var diags hcl.Diagnostics
	if isJSONSyntax(path) {
		file, diags = hcljson.Parse(src, path)
	} else {
		file, diags = hclsyntax.ParseConfig(src, path, hcl.InitialPos)
	}
	if diags.HasErrors() {
		return invalid("parse", diags), nil
	}
	content, diags := applySchema(file.Body, schema)
	if diags.HasErrors() {
		return invalid("schema", diags), nil
	}
	if diags := analyzeContent(content); diags.HasErrors() {
		return invalid("analysis", diags), nil
	}
	body, diags := evalContent(content, settings.ctx)
	if diags.HasErrors() {
		return invalid("eval", diags), nil
	}
	return object{"valid": true, "body": body}, nil
}

// The schema format of the context file.
type bodySchemaDecl struct {
	Mode       string            `json:"mode"`
	Attributes []json.RawMessage `json:"attributes"`
	Blocks     []json.RawMessage `json:"blocks"`
	Remain     json.RawMessage   `json:"remain"`
}

type attributeSchemaDecl struct {
	Name     *string         `json:"name"`
	Required bool            `json:"required"`
	Analysis json.RawMessage `json:"analysis"`
}

type blockSchemaDecl struct {
	Type   *string         `json:"type"`
	Labels []*string       `json:"labels"`
	Body   json.RawMessage `json:"body"`
}

// bodySchema says how to process a body: with hashicorp/hcl's Content,
// PartialContent or JustAttributes, and how to process the bodies that
// produces.
type bodySchema struct {
	mode     string
	schema   *hcl.BodySchema
	analyses map[string]*analysis   // by attribute name, for attributes that are analyzed
	blocks   map[string]*bodySchema // by block type
	remain   *bodySchema            // for the body PartialContent leaves, if any
}

func newBodySchema(data json.RawMessage) (*bodySchema, error) {
	var decl bodySchemaDecl
	if err := unmarshalObject(data, &decl); err != nil {
		return nil, err
	}
	s := &bodySchema{mode: decl.Mode, schema: &hcl.BodySchema{}, analyses: map[string]*analysis{}, blocks: map[string]*bodySchema{}}
	switch decl.Mode {
	case "exhaustive", "partial":
	case "dynamic-attributes":
		if decl.Attributes != nil || decl.Blocks != nil || decl.Remain != nil {
			return nil, errors.New("dynamic-attributes mode takes no attributes, blocks or remain")
		}
		return s, nil
	default:
		return nil, fmt.Errorf("unknown mode %q", decl.Mode)
	}
	listed := map[string]int{}
	for _, raw := range decl.Attributes {
		var attr attributeSchemaDecl
		if err := unmarshalObject(raw, &attr); err != nil {
			return nil, fmt.Errorf("attribute: %w", err)
		}
		if attr.Name == nil {
			return nil, errors.New(`attribute without a "name"`)
		}
		listed[*attr.Name]++
		if attr.Analysis != nil {
			a, err := newAnalysis(attr.Analysis)
			if err != nil {
				return nil, fmt.Errorf("attribute %q: analysis: %w", *attr.Name, err)
			}
			s.analyses[*attr.Name] = a
		}
		s.schema.Attributes = append(s.schema.Attributes, hcl.AttributeSchema{Name: *attr.Name, Required: attr.Required})
	}
	for name := range s.analyses {
		if listed[name] > 1 {
			return nil, fmt.Errorf("attribute %q has an analysis, so it can only be listed once", name)
		}
	}
	for _, raw := range decl.Blocks {
		var block blockSchemaDecl
		if err := unmarshalObject(raw, &block); err != nil {
			return nil, fmt.Errorf("block: %w", err)
		}
		switch {
		case block.Type == nil:
			return nil, errors.New(`block without a "type"`)
		case block.Body == nil:
			return nil, fmt.Errorf("block %q: missing body", *block.Type)
		}
		if _, exists := s.blocks[*block.Type]; exists {
			return nil, fmt.Errorf("block type %q is in the schema twice", *block.Type)
		}
		body, err := newBodySchema(block.Body)
		if err != nil {
			return nil, fmt.Errorf("block %q: %w", *block.Type, err)
		}
		labels := make([]string, len(block.Labels))
		for i, label := range block.Labels {
			if label == nil {
				return nil, fmt.Errorf("block %q: a label name is null", *block.Type)
			}
			labels[i] = *label
		}
		s.schema.Blocks = append(s.schema.Blocks, hcl.BlockHeaderSchema{Type: *block.Type, LabelNames: labels})
		s.blocks[*block.Type] = body
	}
	if decl.Remain != nil {
		if decl.Mode != "partial" {
			return nil, errors.New("only partial mode has a remain schema")
		}
		remain, err := newBodySchema(decl.Remain)
		if err != nil {
			return nil, fmt.Errorf("remain: %w", err)
		}
		s.remain = remain
	}
	return s, nil
}

// decodedBody is the result of applying a bodySchema to a body.
type decodedBody struct {
	attributes hcl.Attributes
	analyses   map[string]*analysis // from the schema
	analyzed   map[string]*analyzed // the results of analyzing those attributes
	blocks     []decodedBlock
	remain     *decodedBody
}

type decodedBlock struct {
	block *hcl.Block
	body  *decodedBody
}

func applySchema(body hcl.Body, s *bodySchema) (*decodedBody, hcl.Diagnostics) {
	out := &decodedBody{analyses: s.analyses}
	var content *hcl.BodyContent
	var diags hcl.Diagnostics
	switch s.mode {
	case "dynamic-attributes":
		out.attributes, diags = body.JustAttributes()
		return out, diags
	case "exhaustive":
		content, diags = body.Content(s.schema)
	case "partial":
		var remain hcl.Body
		content, remain, diags = body.PartialContent(s.schema)
		if s.remain != nil {
			var remainDiags hcl.Diagnostics
			out.remain, remainDiags = applySchema(remain, s.remain)
			diags = append(diags, remainDiags...)
		}
	}
	out.attributes = content.Attributes
	for _, block := range content.Blocks {
		inner, blockDiags := applySchema(block.Body, s.blocks[block.Type])
		diags = append(diags, blockDiags...)
		out.blocks = append(out.blocks, decodedBlock{block: block, body: inner})
	}
	return out, diags
}

func sortedNames(attrs hcl.Attributes) []string {
	names := make([]string, 0, len(attrs))
	for name := range attrs {
		names = append(names, name)
	}
	sort.Strings(names) // keeps the order of reported errors stable
	return names
}

// analyzeContent statically analyzes the attributes whose schema asks for it,
// in the body, its blocks and its remaining body, without evaluating anything.
func analyzeContent(d *decodedBody) hcl.Diagnostics {
	var diags hcl.Diagnostics
	d.analyzed = map[string]*analyzed{}
	for _, name := range sortedNames(d.attributes) {
		if a, ok := d.analyses[name]; ok {
			result, analysisDiags := analyze(d.attributes[name].Expr, a)
			diags = append(diags, analysisDiags...)
			d.analyzed[name] = result
		}
	}
	for _, b := range d.blocks {
		diags = append(diags, analyzeContent(b.body)...)
	}
	if d.remain != nil {
		diags = append(diags, analyzeContent(d.remain)...)
	}
	return diags
}

func evalContent(d *decodedBody, ctx *hcl.EvalContext) (object, hcl.Diagnostics) {
	var diags hcl.Diagnostics
	attrs := object{}
	for _, name := range sortedNames(d.attributes) {
		if result, ok := d.analyzed[name]; ok {
			encoded, resultDiags := result.eval(ctx)
			diags = append(diags, resultDiags...)
			attrs[name] = encoded
			continue
		}
		val, valDiags := d.attributes[name].Expr.Value(ctx)
		diags = append(diags, valDiags...)
		attrs[name] = encodeValue(val)
	}
	blocks := []any{}
	for _, b := range d.blocks {
		inner, blockDiags := evalContent(b.body, ctx)
		diags = append(diags, blockDiags...)
		labels := b.block.Labels
		if labels == nil {
			labels = []string{}
		}
		blocks = append(blocks, object{"type": b.block.Type, "labels": labels, "body": inner})
	}
	out := object{"attributes": attrs, "blocks": blocks}
	if d.remain != nil {
		remain, remainDiags := evalContent(d.remain, ctx)
		diags = append(diags, remainDiags...)
		out["remain"] = remain
	}
	return out, diags
}

// The analysis format of attribute schemata.
type analysisDecl struct {
	Kind      string          `json:"kind"`
	Elements  json.RawMessage `json:"elements"`
	Keys      json.RawMessage `json:"keys"`
	Values    json.RawMessage `json:"values"`
	Arguments json.RawMessage `json:"arguments"`
}

// analysis says how to analyze an expression statically. A nil analysis, like
// one of kind "value", evaluates the expression instead.
type analysis struct {
	kind                              string
	elements, keys, values, arguments *analysis
}

func newAnalysis(data json.RawMessage) (*analysis, error) {
	var decl analysisDecl
	if err := unmarshalObject(data, &decl); err != nil {
		return nil, err
	}
	a := &analysis{kind: decl.Kind}
	parts := []struct {
		field string
		raw   json.RawMessage
		into  **analysis
		kind  string
	}{
		{"elements", decl.Elements, &a.elements, "static-list"},
		{"keys", decl.Keys, &a.keys, "static-map"},
		{"values", decl.Values, &a.values, "static-map"},
		{"arguments", decl.Arguments, &a.arguments, "static-call"},
	}
	switch decl.Kind {
	case "value", "static-list", "static-map", "static-call", "static-traversal", "type", "type-constraint",
		"type-constraint-with-defaults":
	default:
		return nil, fmt.Errorf("unknown analysis kind %q", decl.Kind)
	}
	for _, part := range parts {
		if part.raw == nil {
			continue
		}
		if part.kind != decl.Kind {
			return nil, fmt.Errorf("%s analysis has no %q", decl.Kind, part.field)
		}
		inner, err := newAnalysis(part.raw)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", part.field, err)
		}
		*part.into = inner
	}
	return a, nil
}

// analyzed is the result of analyzing an expression: the expressions the
// analysis found are evaluated later, or analyzed further.
type analyzed struct {
	kind      string
	expr      hcl.Expression // for "value"
	items     []*analyzed    // list elements or call arguments
	pairs     [][2]*analyzed // map keys and values
	name      string         // the called function
	traversal hcl.Traversal
	ty        cty.Type // for the type expression kinds
}

func analyze(expr hcl.Expression, a *analysis) (*analyzed, hcl.Diagnostics) {
	if a == nil || a.kind == "value" {
		return &analyzed{kind: "value", expr: expr}, nil
	}
	out := &analyzed{kind: a.kind}
	var diags hcl.Diagnostics
	switch a.kind {
	case "static-list":
		exprs, listDiags := hcl.ExprList(expr)
		if listDiags.HasErrors() {
			return nil, listDiags
		}
		for _, element := range exprs {
			item, itemDiags := analyze(element, a.elements)
			diags = append(diags, itemDiags...)
			out.items = append(out.items, item)
		}
	case "static-map":
		pairs, mapDiags := hcl.ExprMap(expr)
		if mapDiags.HasErrors() {
			return nil, mapDiags
		}
		for _, pair := range pairs {
			key, keyDiags := analyze(pair.Key, a.keys)
			value, valueDiags := analyze(pair.Value, a.values)
			diags = append(diags, keyDiags...)
			diags = append(diags, valueDiags...)
			out.pairs = append(out.pairs, [2]*analyzed{key, value})
		}
	case "static-call":
		call, callDiags := hcl.ExprCall(expr)
		if callDiags.HasErrors() {
			return nil, callDiags
		}
		out.name = call.Name
		for _, argument := range call.Arguments {
			item, itemDiags := analyze(argument, a.arguments)
			diags = append(diags, itemDiags...)
			out.items = append(out.items, item)
		}
	case "static-traversal":
		traversal, traversalDiags := hcl.AbsTraversalForExpr(expr)
		if traversalDiags.HasErrors() {
			return nil, traversalDiags
		}
		out.traversal = traversal
	case "type", "type-constraint", "type-constraint-with-defaults":
		var typeDiags hcl.Diagnostics
		switch a.kind {
		case "type":
			out.ty, typeDiags = typeexpr.Type(expr)
		case "type-constraint":
			out.ty, typeDiags = typeexpr.TypeConstraint(expr)
		default:
			out.ty, _, typeDiags = typeexpr.TypeConstraintWithDefaults(expr)
		}
		if typeDiags.HasErrors() {
			return nil, typeDiags
		}
	}
	return out, diags
}

func (n *analyzed) eval(ctx *hcl.EvalContext) (any, hcl.Diagnostics) {
	var diags hcl.Diagnostics
	evalItems := func(items []*analyzed) []any {
		out := []any{}
		for _, item := range items {
			encoded, itemDiags := item.eval(ctx)
			diags = append(diags, itemDiags...)
			out = append(out, encoded)
		}
		return out
	}
	switch n.kind {
	case "value":
		val, valDiags := n.expr.Value(ctx)
		return encodeValue(val), valDiags
	case "static-list":
		items := evalItems(n.items)
		return object{"static_list": items}, diags
	case "static-map":
		pairs := []any{}
		for _, pair := range n.pairs {
			kv := evalItems(pair[:])
			pairs = append(pairs, object{"key": kv[0], "value": kv[1]})
		}
		return object{"static_map": pairs}, diags
	case "static-call":
		arguments := evalItems(n.items)
		return object{"static_call": object{"name": n.name, "arguments": arguments}}, diags
	case "type", "type-constraint", "type-constraint-with-defaults":
		return object{"type": typeJSON(n.ty)}, nil
	}
	steps := []any{}
	for _, step := range n.traversal {
		switch step := step.(type) {
		case hcl.TraverseRoot:
			steps = append(steps, object{"root": step.Name})
		case hcl.TraverseAttr:
			steps = append(steps, object{"attr": step.Name})
		case hcl.TraverseIndex:
			steps = append(steps, object{"index": encodeValue(step.Key)})
		default:
			panic(unsupported{fmt.Sprintf("traversal step %T", step)})
		}
	}
	return object{"static_traversal": steps}, diags
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

// The context file given to eval and decode. Its declarations are decoded one level at a
// time with unmarshalObject, and its values with decodeValue, so that every
// level is checked strictly.
type evalContext struct {
	Variables      map[string]json.RawMessage `json:"variables"`
	Functions      map[string]json.RawMessage `json:"functions"`
	Schema         json.RawMessage            `json:"schema"`
	EvaluationMode *string                    `json:"evaluation_mode"`
}

// evalSettings is what a context file sets up.
type evalSettings struct {
	ctx    *hcl.EvalContext // nil in literal-only mode, as hashicorp/hcl expects
	schema json.RawMessage
}

func emptyEvalContext() *hcl.EvalContext {
	return &hcl.EvalContext{Variables: map[string]cty.Value{}, Functions: map[string]function.Function{}}
}

type functionDecl struct {
	Params        []json.RawMessage `json:"params"`
	VariadicParam json.RawMessage   `json:"variadic_param"`
	Result        json.RawMessage   `json:"result"`
	Extension     *string           `json:"extension"`
}

type paramDecl struct {
	Name             string          `json:"name"`
	Type             json.RawMessage `json:"type"`
	AllowNull        bool            `json:"allow_null"`
	AllowUnknown     bool            `json:"allow_unknown"`
	AllowDynamicType bool            `json:"allow_dynamic_type"`
}

type fixedResult struct {
	Value json.RawMessage `json:"value"`
	Error *string         `json:"error"`
}

// unmarshalObject decodes a JSON object into the struct v points to. Unlike
// json.Unmarshal alone, it rejects null, fields the struct doesn't have, fields
// set to null, and field names that differ from the struct's only in case.
func unmarshalObject(data []byte, v any) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	if fields == nil {
		return errors.New("expected an object, not null")
	}
	known := map[string]bool{}
	t := reflect.TypeOf(v).Elem()
	for i := 0; i < t.NumField(); i++ {
		name, _, _ := strings.Cut(t.Field(i).Tag.Get("json"), ",")
		known[name] = true
	}
	for name, raw := range fields {
		switch {
		case !known[name]:
			return fmt.Errorf("unknown field %q", name)
		case string(raw) == "null":
			return fmt.Errorf("field %q is null", name)
		}
	}
	return json.Unmarshal(data, v)
}

func readContext(path string) (evalSettings, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return evalSettings{}, err
	}
	var decl evalContext
	if err := unmarshalObject(data, &decl); err != nil {
		return evalSettings{}, fmt.Errorf("%s: %w", path, err)
	}
	settings := evalSettings{ctx: emptyEvalContext(), schema: decl.Schema}
	for name, raw := range decl.Variables {
		if settings.ctx.Variables[name], err = decodeValue(raw); err != nil {
			return evalSettings{}, fmt.Errorf("%s: variable %q: %w", path, name, err)
		}
	}
	for name, raw := range decl.Functions {
		if settings.ctx.Functions[name], err = newFunction(raw); err != nil {
			return evalSettings{}, fmt.Errorf("%s: function %q: %w", path, name, err)
		}
	}
	switch {
	case decl.EvaluationMode == nil:
	case *decl.EvaluationMode == "literal-only":
		if decl.Variables != nil || decl.Functions != nil {
			return evalSettings{}, fmt.Errorf("%s: literal-only mode has no variables or functions", path)
		}
		settings.ctx = nil
	default:
		return evalSettings{}, fmt.Errorf("%s: the only evaluation mode is %q, not %q", path, "literal-only", *decl.EvaluationMode)
	}
	return settings, nil
}

// newFunction builds a go-cty function from a declaration. Its Type and Impl
// only describe the result: hashicorp/hcl checks the number of arguments and
// converts them to the parameter types, and go-cty's Function.Call handles
// null, unknown and dynamic arguments before calling Impl.
func newFunction(data json.RawMessage) (function.Function, error) {
	var decl functionDecl
	if err := unmarshalObject(data, &decl); err != nil {
		return function.Function{}, err
	}
	if decl.Extension != nil {
		if decl.Params != nil || decl.VariadicParam != nil || decl.Result != nil {
			return function.Function{}, errors.New("an extension function has no other fields")
		}
		switch *decl.Extension {
		case "convert":
			return typeexpr.ConvertFunc, nil
		case "convert-with-defaults":
			return convertWithDefaultsFunc, nil
		}
		return function.Function{}, fmt.Errorf("unknown extension function %q", *decl.Extension)
	}
	spec := &function.Spec{Params: []function.Parameter{}}
	for i, raw := range decl.Params {
		param, err := newParameter(raw, fmt.Sprintf("param%d", i))
		if err != nil {
			return function.Function{}, fmt.Errorf("parameter %d: %w", i, err)
		}
		spec.Params = append(spec.Params, param)
	}
	if decl.VariadicParam != nil {
		param, err := newParameter(decl.VariadicParam, "variadic")
		if err != nil {
			return function.Function{}, fmt.Errorf("variadic parameter: %w", err)
		}
		spec.VarParam = &param
	}

	var fixed fixedResult
	switch {
	case string(decl.Result) == `"arguments"`:
		// The result is a tuple of the arguments the function receives, so its
		// type is the tuple of their types.
		spec.Type = func(args []cty.Value) (cty.Type, error) {
			types := make([]cty.Type, len(args))
			for i, arg := range args {
				types[i] = arg.Type()
			}
			return cty.Tuple(types), nil
		}
		spec.Impl = func(args []cty.Value, _ cty.Type) (cty.Value, error) {
			return cty.TupleVal(args), nil
		}
	case decl.Result == nil:
		return function.Function{}, errors.New(`missing "result"`)
	case unmarshalObject(decl.Result, &fixed) != nil:
		return function.Function{}, fmt.Errorf("invalid result %s", decl.Result)
	case fixed.Value != nil && fixed.Error == nil:
		v, err := decodeValue(fixed.Value)
		if err != nil {
			return function.Function{}, fmt.Errorf("result value: %w", err)
		}
		spec.Type = function.StaticReturnType(v.Type())
		spec.Impl = func([]cty.Value, cty.Type) (cty.Value, error) {
			return v, nil
		}
	case fixed.Error != nil && fixed.Value == nil:
		message := *fixed.Error
		spec.Type = function.StaticReturnType(cty.DynamicPseudoType)
		spec.Impl = func([]cty.Value, cty.Type) (cty.Value, error) {
			return cty.NilVal, errors.New(message)
		}
	default:
		return function.Function{}, errors.New(`result needs exactly one of "value" and "error"`)
	}
	return function.New(spec), nil
}

// typeWithDefaults is a type constraint and the defaults for its optional
// attributes, as typeexpr.TypeConstraintWithDefaults gives them.
type typeWithDefaults struct {
	ty       cty.Type
	defaults *typeexpr.Defaults
}

// typeConstraintWithDefaultsType is like typeexpr.TypeConstraintType, except
// that a type expression given for it can also give optional attributes
// default values.
var typeConstraintWithDefaultsType cty.Type

// convertWithDefaultsFunc is typeexpr.ConvertFunc, except that it applies the
// defaults of its type constraint to the value before converting it, the way
// applications apply typeexpr.Defaults.
var convertWithDefaultsFunc function.Function

func init() {
	typeConstraintWithDefaultsType = cty.CapsuleWithOps("type constraint with defaults", reflect.TypeOf(typeWithDefaults{}), &cty.CapsuleOps{
		ExtensionData: func(key any) any {
			if key != customdecode.CustomExpressionDecoder {
				return nil
			}
			return customdecode.CustomExpressionDecoderFunc(func(expr hcl.Expression, _ *hcl.EvalContext) (cty.Value, hcl.Diagnostics) {
				ty, defaults, diags := typeexpr.TypeConstraintWithDefaults(expr)
				if diags.HasErrors() {
					return cty.NilVal, diags
				}
				return cty.CapsuleVal(typeConstraintWithDefaultsType, &typeWithDefaults{ty, defaults}), nil
			})
		},
	})
	convertValue := func(args []cty.Value) (cty.Value, error) {
		target := args[1].EncapsulatedValue().(*typeWithDefaults)
		value := args[0]
		if target.defaults != nil {
			value = target.defaults.Apply(value)
		}
		converted, err := convert.Convert(value, target.ty)
		if err != nil {
			return cty.NilVal, function.NewArgError(0, err)
		}
		return converted, nil
	}
	// The parameters are the same as typeexpr.ConvertFunc's.
	convertWithDefaultsFunc = function.New(&function.Spec{
		Params: []function.Parameter{
			{Name: "value", Type: cty.DynamicPseudoType, AllowNull: true, AllowDynamicType: true},
			{Name: "type", Type: typeConstraintWithDefaultsType},
		},
		Type: func(args []cty.Value) (cty.Type, error) {
			converted, err := convertValue(args)
			if err != nil {
				return cty.NilType, err
			}
			return converted.Type(), nil
		},
		Impl: func(args []cty.Value, _ cty.Type) (cty.Value, error) {
			return convertValue(args)
		},
	})
}

func newParameter(data json.RawMessage, defaultName string) (function.Parameter, error) {
	var decl paramDecl
	if err := unmarshalObject(data, &decl); err != nil {
		return function.Parameter{}, err
	}
	if decl.Type == nil {
		return function.Parameter{}, errors.New(`missing "type"`)
	}
	ty, err := decodeType(decl.Type)
	if err != nil {
		return function.Parameter{}, fmt.Errorf("type: %w", err)
	}
	name := decl.Name
	if name == "" {
		name = defaultName
	}
	return function.Parameter{
		Name:             name,
		Type:             ty,
		AllowNull:        decl.AllowNull,
		AllowUnknown:     decl.AllowUnknown,
		AllowDynamicType: decl.AllowDynamicType,
	}, nil
}

// decodeType decodes a type of the context file. Only type expression results
// have object types with optional attributes, so the notation for them is an
// error here, and so is anything else go-cty would panic on.
func decodeType(raw json.RawMessage) (ty cty.Type, err error) {
	// check follows the type notation, leaving anything else it finds to go-cty.
	var check func(any) error
	check = func(node any) error {
		items, ok := node.([]any)
		if !ok || len(items) == 0 {
			return nil
		}
		var children []any
		switch items[0] {
		case "object":
			if len(items) == 3 {
				return errors.New("only type expression results have object types with optional attributes")
			}
			if attrs, ok := items[len(items)-1].(map[string]any); ok && len(items) == 2 {
				for _, attr := range attrs {
					children = append(children, attr)
				}
			}
		case "tuple":
			if elems, ok := items[len(items)-1].([]any); ok && len(items) == 2 {
				children = elems
			}
		case "list", "set", "map":
			if len(items) == 2 {
				children = items[1:]
			}
		}
		for _, child := range children {
			if err := check(child); err != nil {
				return err
			}
		}
		return nil
	}
	var node any
	if err := json.Unmarshal(raw, &node); err != nil {
		return cty.NilType, err
	}
	if err := check(node); err != nil {
		return cty.NilType, err
	}
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("invalid type %s: %v", raw, r)
		}
	}()
	return ctyjson.UnmarshalType(raw)
}

// valueKinds are the keys that name the kind of a value in the protocol.
var valueKinds = map[string]bool{
	"string": true, "number": true, "bool": true, "null": true, "unknown": true,
	"tuple": true, "object": true, "list": true, "set": true, "map": true,
}

// decodeValue decodes a value in the protocol's encoding: an object with one key
// naming the kind of value, and an element_type for lists, sets and maps.
func decodeValue(data json.RawMessage) (cty.Value, error) {
	var node map[string]json.RawMessage
	if err := json.Unmarshal(data, &node); err != nil {
		return cty.NilVal, err
	}
	var kind string
	for key := range node {
		if key != "element_type" {
			if !valueKinds[key] || kind != "" {
				return cty.NilVal, fmt.Errorf("not a value: %s", data)
			}
			kind = key
		}
	}
	raw := node[kind]
	_, hasElementType := node["element_type"]
	switch {
	case kind == "" || string(raw) == "null":
		return cty.NilVal, fmt.Errorf("not a value: %s", data)
	case hasElementType != (kind == "list" || kind == "set" || kind == "map"):
		return cty.NilVal, fmt.Errorf("lists, sets and maps need an element_type, and other values have none: %s", data)
	}
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
		switch s {
		case "Infinity":
			return cty.PositiveInfinity, nil
		case "-Infinity":
			return cty.NegativeInfinity, nil
		}
		return cty.ParseNumberVal(s)
	case "bool":
		var b bool
		err := json.Unmarshal(raw, &b)
		return cty.BoolVal(b), err
	case "null", "unknown":
		ty, err := decodeType(raw)
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
	panic("unreachable: every value kind is handled")
}

func sequence(kind string, rawType json.RawMessage, elems []cty.Value) (v cty.Value, err error) {
	if kind == "tuple" {
		return cty.TupleVal(elems), nil
	}
	ety, err := decodeType(rawType)
	if err != nil {
		return cty.NilVal, fmt.Errorf("%s element_type: %w", kind, err)
	}
	for i, elem := range elems {
		if !elem.Type().Equals(ety) {
			return cty.NilVal, fmt.Errorf("%s element %d is %s, but element_type is %s", kind, i, elem.Type().FriendlyName(), ety.FriendlyName())
		}
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
	ety, err := decodeType(rawType)
	if err != nil {
		return cty.NilVal, fmt.Errorf("map element_type: %w", err)
	}
	for key, elem := range attrs {
		if !elem.Type().Equals(ety) {
			return cty.NilVal, fmt.Errorf("map element %q is %s, but element_type is %s", key, elem.Type().FriendlyName(), ety.FriendlyName())
		}
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
