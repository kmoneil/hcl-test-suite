//go:build ignore

// This file puts the operators of tenon (github.com/kmoneil/tenon) in place of
// go-cty's in hashicorp/hcl's evaluator, as tenon's proof module does. The
// ignore constraint keeps it out of the default build and out of go mod tidy,
// which reads files behind any other constraint and would add tenon and
// go-cty v1.19.0 to go.mod and opentofu.mod. Name both files to build it:
//
//	go build -modfile=tenon.mod -o ../../bin/hcl-tenon-adapter main.go tenon.go

package main

import (
	"github.com/hashicorp/hcl/v2/hclsyntax"
	"github.com/kmoneil/tenon"
	"github.com/kmoneil/tenon/ctytenon"
	"github.com/kmoneil/tenon/stdlib"
)

func init() {
	operatorModule = "kmoneil/tenon"
	// The parser holds the original operations, so each one's Impl is set
	// rather than the variable replaced.
	for _, o := range []struct {
		op *hclsyntax.Operation
		f  tenon.Function
	}{
		{hclsyntax.OpLogicalOr, stdlib.OrFunc},
		{hclsyntax.OpLogicalAnd, stdlib.AndFunc},
		{hclsyntax.OpLogicalNot, stdlib.NotFunc},
		{hclsyntax.OpEqual, stdlib.EqualFunc},
		{hclsyntax.OpNotEqual, stdlib.NotEqualFunc},
		{hclsyntax.OpGreaterThan, stdlib.GreaterThanFunc},
		{hclsyntax.OpGreaterThanOrEqual, stdlib.GreaterThanOrEqualToFunc},
		{hclsyntax.OpLessThan, stdlib.LessThanFunc},
		{hclsyntax.OpLessThanOrEqual, stdlib.LessThanOrEqualToFunc},
		{hclsyntax.OpAdd, stdlib.AddFunc},
		{hclsyntax.OpSubtract, stdlib.SubtractFunc},
		{hclsyntax.OpMultiply, stdlib.MultiplyFunc},
		{hclsyntax.OpDivide, stdlib.DivideFunc},
		{hclsyntax.OpModulo, stdlib.ModuloFunc},
		{hclsyntax.OpNegate, stdlib.NegateFunc},
	} {
		f, err := ctytenon.Bridge{}.FunctionToCty(o.f, tenon.Unsafe)
		if err != nil {
			panic(err)
		}
		o.op.Impl = f
	}
}
