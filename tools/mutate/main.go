// Command mutate lists the mutants of one Go package as JSON, for
// tools/mutation.py. A mutant is one small change to the package's source:
//
//	cond_boundary   < and <=, > and >= swapped
//	cond_negate     == and !=, < and >=, <= and >, > and <= swapped
//	logical         && and || swapped
//	arith           + and -, * and /, += and -=, *= and /= swapped; % becomes *
//	incdec          ++ and -- swapped
//	negation        a unary - removed
//	not_remove      a unary ! removed
//	bool_lit        true and false swapped
//	break_continue  break and continue swapped inside a loop
//	if_true         an if condition c becomes true || (c)
//	if_false        an if condition c becomes false && (c)
//	stmt_remove     a call, assignment or ++/-- statement removed
//
//	mutate -dir <directory to run go list in> [-modfile <file>] -pkg <import path>
//
// Each mutant replaces the bytes [start, end) of one file. Its key, the file,
// line and column of the change and the operator, stays the same as long as
// the package's source does. Positions are also reported adjusted by //line
// directives, because coverage profiles use those. In files that Ragel
// generated, only code that line directives map back to the .rl source is
// mutated, which leaves the generated state machine alone. Files with a "Code
// generated ... DO NOT EDIT." comment are skipped.
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/importer"
	"go/parser"
	"go/token"
	"go/types"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

type listedPackage struct {
	ImportPath string
	Dir        string
	Export     string
	GoFiles    []string
}

type mutant struct {
	ID          int    `json:"id"`
	Key         string `json:"key"`
	Op          string `json:"op"`
	File        string `json:"file"`  // the file that is changed
	Start       int    `json:"start"` // byte offsets of the change in File
	End         int    `json:"end"`
	Replacement string `json:"replacement"`
	Orig        string `json:"orig"`
	CovFile     string `json:"cov_file"` // file name after //line directives
	Line        int    `json:"line"`     // line after //line directives
	Col         int    `json:"col"`      // column after //line directives, 0 if they give none
	Func        string `json:"func"`
	Source      string `json:"source"` // the changed line, as written
}

var generatedRE = regexp.MustCompile(`(?m)^// Code generated .* DO NOT EDIT\.$`)

func main() {
	dir := flag.String("dir", ".", "directory to run go list in")
	modfile := flag.String("modfile", "", "modfile for go list")
	pkgPath := flag.String("pkg", "", "import path of the package to mutate")
	flag.Parse()

	args := []string{"list", "-export", "-deps", "-json=ImportPath,Dir,Export,GoFiles"}
	if *modfile != "" {
		args = append(args, "-modfile="+*modfile)
	}
	cmd := exec.Command("go", append(args, *pkgPath)...)
	cmd.Dir = *dir
	cmd.Stderr = os.Stderr
	out, err := cmd.Output()
	check(err)
	exports := map[string]string{}
	var target listedPackage
	dec := json.NewDecoder(bytes.NewReader(out))
	for {
		var p listedPackage
		if err := dec.Decode(&p); err == io.EOF {
			break
		} else {
			check(err)
		}
		exports[p.ImportPath] = p.Export
		if p.ImportPath == *pkgPath {
			target = p
		}
	}
	if target.Dir == "" {
		check(fmt.Errorf("package %s not found", *pkgPath))
	}

	fset := token.NewFileSet()
	var files []*ast.File
	srcs := map[string][]byte{}
	for _, name := range target.GoFiles {
		path := filepath.Join(target.Dir, name)
		src, err := os.ReadFile(path)
		check(err)
		f, err := parser.ParseFile(fset, path, src, parser.ParseComments)
		check(err)
		files = append(files, f)
		srcs[path] = src
	}
	lookup := func(path string) (io.ReadCloser, error) {
		if e := exports[path]; e != "" {
			return os.Open(e)
		}
		return nil, fmt.Errorf("no export data for %s", path)
	}
	info := &types.Info{Types: map[ast.Expr]types.TypeAndValue{}, Uses: map[*ast.Ident]types.Object{}}
	conf := types.Config{Importer: importer.ForCompiler(fset, "gc", lookup)}
	pkg, err := conf.Check(*pkgPath, fset, files, info)
	check(err)

	g := &generator{fset: fset, info: info, pkg: pkg, srcs: srcs}
	for _, f := range files {
		g.file(f)
	}
	sort.SliceStable(g.out, func(i, j int) bool {
		if g.out[i].File != g.out[j].File {
			return g.out[i].File < g.out[j].File
		}
		return g.out[i].Start < g.out[j].Start
	})
	keys := map[string]bool{}
	for i := range g.out {
		g.out[i].ID = i
		if keys[g.out[i].Key] {
			check(fmt.Errorf("two mutants have the key %s", g.out[i].Key))
		}
		keys[g.out[i].Key] = true
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", " ")
	check(enc.Encode(g.out))
}

func check(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "mutate:", err)
		os.Exit(1)
	}
}

type generator struct {
	fset *token.FileSet
	info *types.Info
	pkg  *types.Package
	srcs map[string][]byte
	out  []mutant

	src      []byte // the file being walked
	ragel    bool   // whether Ragel generated it
	funcName string // the function being walked
}

func (g *generator) file(f *ast.File) {
	g.src = g.srcs[g.fset.File(f.Pos()).Name()]
	if generatedRE.Match(g.src) {
		return
	}
	g.ragel = bytes.HasPrefix(g.src, []byte("//line ")) || bytes.Contains(g.src, []byte("\n//line "))
	for _, d := range f.Decls {
		fd, ok := d.(*ast.FuncDecl)
		if !ok || fd.Body == nil {
			continue
		}
		g.funcName = fd.Name.Name
		if fd.Recv != nil && len(fd.Recv.List) > 0 {
			g.funcName = types.ExprString(fd.Recv.List[0].Type) + "." + fd.Name.Name
		}
		g.walk(fd.Body, nil)
	}
}

// handWritten reports whether pos is in code written by hand: anywhere in a
// file Ragel didn't generate, or where a line directive points back to the .rl
// file.
func (g *generator) handWritten(pos token.Pos) bool {
	return !g.ragel || strings.HasSuffix(g.fset.Position(pos).Filename, ".rl")
}

func (g *generator) add(op string, start, end token.Pos, repl string) {
	if !g.handWritten(start) {
		return
	}
	s, e := g.fset.File(start).Offset(start), g.fset.File(end).Offset(end)
	if string(g.src[s:e]) == repl {
		return // removing "_ = x" would put back "_ = x"
	}
	raw := g.fset.PositionFor(start, false)
	adj := g.fset.Position(start)
	lineStart := bytes.LastIndexByte(g.src[:s], '\n') + 1
	lineEnd := bytes.IndexByte(g.src[s:], '\n')
	if lineEnd < 0 {
		lineEnd = len(g.src) - s
	}
	file := filepath.Base(raw.Filename)
	g.out = append(g.out, mutant{
		Key: fmt.Sprintf("%s:%d:%d %s", file, raw.Line, raw.Column, op),
		Op:  op, File: file, Start: s, End: e, Replacement: repl, Orig: string(g.src[s:e]),
		CovFile: filepath.Base(adj.Filename), Line: adj.Line, Col: adj.Column, Func: g.funcName,
		Source: strings.TrimSpace(string(g.src[lineStart : s+lineEnd])),
	})
}

func (g *generator) text(n ast.Node) string {
	f := g.fset.File(n.Pos())
	return string(g.src[f.Offset(n.Pos()):f.Offset(n.End())])
}

// walk visits n. loops holds the enclosing loop and switch statements,
// innermost last, to tell where break and continue can be swapped.
func (g *generator) walk(n ast.Node, loops []ast.Node) {
	ast.Inspect(n, func(c ast.Node) bool {
		switch c := c.(type) {
		case *ast.FuncLit:
			g.walk(c.Body, nil) // a closure starts a new break/continue context
			return false
		case *ast.ForStmt:
			if c == n {
				return true
			}
			for _, part := range []ast.Node{c.Init, c.Cond, c.Post} {
				if part != nil {
					g.walk(part, nil)
				}
			}
			if c.Post != nil {
				g.stmtList([]ast.Stmt{c.Post})
			}
			g.walk(c.Body, append(loops[:len(loops):len(loops)], c))
			return false
		case *ast.RangeStmt:
			if c == n {
				return true
			}
			g.walk(c.X, nil)
			g.walk(c.Body, append(loops[:len(loops):len(loops)], c))
			return false
		case *ast.SwitchStmt:
			if c.Init != nil {
				g.walk(c.Init, nil)
			}
			if c.Tag != nil {
				g.walk(c.Tag, nil)
			}
			g.walk(c.Body, append(loops[:len(loops):len(loops)], c))
			return false
		case *ast.TypeSwitchStmt:
			if c.Init != nil {
				g.walk(c.Init, nil)
			}
			g.walk(c.Body, append(loops[:len(loops):len(loops)], c))
			return false
		case *ast.SelectStmt:
			g.walk(c.Body, append(loops[:len(loops):len(loops)], c))
			return false
		case *ast.BranchStmt:
			g.branch(c, loops)
		case *ast.IfStmt:
			cond := g.text(c.Cond)
			g.add("if_true", c.Cond.Pos(), c.Cond.End(), "true || ("+cond+")")
			g.add("if_false", c.Cond.Pos(), c.Cond.End(), "false && ("+cond+")")
		case *ast.BinaryExpr:
			g.binary(c)
		case *ast.UnaryExpr:
			switch c.Op {
			case token.SUB:
				g.add("negation", c.OpPos, c.OpPos+1, "")
			case token.NOT:
				g.add("not_remove", c.OpPos, c.OpPos+1, "")
			}
		case *ast.IncDecStmt:
			if c.Tok == token.INC {
				g.add("incdec", c.TokPos, c.TokPos+2, "--")
			} else {
				g.add("incdec", c.TokPos, c.TokPos+2, "++")
			}
		case *ast.AssignStmt:
			g.assignOp(c)
		case *ast.Ident:
			g.boolLit(c)
		case *ast.BlockStmt:
			g.stmtList(c.List)
		case *ast.CaseClause:
			g.stmtList(c.Body)
		case *ast.CommClause:
			g.stmtList(c.Body)
		}
		return true
	})
}

func (g *generator) branch(b *ast.BranchStmt, loops []ast.Node) {
	if b.Label != nil || (b.Tok != token.BREAK && b.Tok != token.CONTINUE) {
		return
	}
	inLoop := false
	for _, l := range loops {
		switch l.(type) {
		case *ast.ForStmt, *ast.RangeStmt:
			inLoop = true
		}
	}
	if !inLoop {
		return
	}
	if b.Tok == token.BREAK {
		g.add("break_continue", b.Pos(), b.End(), "continue")
	} else {
		g.add("break_continue", b.Pos(), b.End(), "break")
	}
}

var swaps = []struct {
	op string
	to map[token.Token]token.Token
}{
	{"cond_boundary", map[token.Token]token.Token{token.LSS: token.LEQ, token.LEQ: token.LSS, token.GTR: token.GEQ,
		token.GEQ: token.GTR}},
	{"cond_negate", map[token.Token]token.Token{token.EQL: token.NEQ, token.NEQ: token.EQL, token.LSS: token.GEQ,
		token.LEQ: token.GTR, token.GTR: token.LEQ, token.GEQ: token.LSS}},
	{"logical", map[token.Token]token.Token{token.LAND: token.LOR, token.LOR: token.LAND}},
	{"arith", map[token.Token]token.Token{token.ADD: token.SUB, token.SUB: token.ADD, token.MUL: token.QUO,
		token.QUO: token.MUL, token.REM: token.MUL}},
}

func (g *generator) isString(e ast.Expr) bool {
	t, ok := g.info.Types[e]
	if !ok {
		return false
	}
	b, ok := t.Type.Underlying().(*types.Basic)
	return ok && b.Info()&types.IsString != 0
}

func (g *generator) binary(e *ast.BinaryExpr) {
	x, okX := g.info.Types[e.X]
	y, okY := g.info.Types[e.Y]
	if okX && okY && x.Value != nil && y.Value != nil {
		return // a constant expression
	}
	for _, s := range swaps {
		to, ok := s.to[e.Op]
		if !ok || (s.op == "arith" && g.isString(e.X)) {
			continue
		}
		g.add(s.op, e.OpPos, e.OpPos+token.Pos(len(e.Op.String())), to.String())
	}
}

func (g *generator) assignOp(s *ast.AssignStmt) {
	repl := map[token.Token]string{token.ADD_ASSIGN: "-=", token.SUB_ASSIGN: "+=", token.MUL_ASSIGN: "/=",
		token.QUO_ASSIGN: "*="}
	r, ok := repl[s.Tok]
	if !ok || len(s.Lhs) != 1 || g.isString(s.Lhs[0]) {
		return
	}
	g.add("arith", s.TokPos, s.TokPos+2, r)
}

func (g *generator) boolLit(id *ast.Ident) {
	obj := g.info.Uses[id]
	if obj == nil || obj.Parent() != types.Universe {
		return
	}
	switch id.Name {
	case "true":
		g.add("bool_lit", id.Pos(), id.End(), "false")
	case "false":
		g.add("bool_lit", id.Pos(), id.End(), "true")
	}
}

// stmtList adds a stmt_remove mutant for each removable statement. A removed
// statement is replaced by blank assignments of the local variables it uses,
// so the mutant still compiles when the statement was their only use.
func (g *generator) stmtList(list []ast.Stmt) {
	for _, s := range list {
		removable := false
		switch s := s.(type) {
		case *ast.ExprStmt:
			if call, ok := s.X.(*ast.CallExpr); ok {
				id, isIdent := call.Fun.(*ast.Ident)
				removable = !isIdent || id.Name != "panic"
			}
		case *ast.AssignStmt:
			removable = s.Tok != token.DEFINE
		case *ast.IncDecStmt:
			removable = true
		}
		if !removable {
			continue
		}
		var keep []string
		seen := map[string]bool{}
		ast.Inspect(s, func(n ast.Node) bool {
			if _, ok := n.(*ast.FuncLit); ok {
				return false
			}
			id, ok := n.(*ast.Ident)
			if !ok {
				return true
			}
			v, ok := g.info.Uses[id].(*types.Var)
			if !ok || v.IsField() || v.Pkg() != g.pkg || v.Parent() == nil || v.Parent() == g.pkg.Scope() ||
				seen[id.Name] {
				return true
			}
			seen[id.Name] = true
			keep = append(keep, "_ = "+id.Name)
			return true
		})
		g.add("stmt_remove", s.Pos(), s.End(), strings.Join(keep, "; "))
	}
}
