//! Connects the test runner to hcl-rs, the Rust implementation of HCL.
//!
//!     hcl-rs-adapter capabilities
//!     hcl-rs-adapter parse <file.hcl>
//!     hcl-rs-adapter eval <file.hcl> [<context.json>]
//!     hcl-rs-adapter validate <file.hcl>
//!
//! The output formats are described in docs/protocol.md.

use std::env;
use std::fs;
use std::process::ExitCode;
use std::sync::OnceLock;

use hcl::eval::{Context, Evaluate, Func, FuncArgs, FuncDef, ParamType};
use hcl::expr::{Expression, FuncName, ObjectKey, Operation, TraversalOperator};
use hcl::template::{Directive, Element};
use hcl::{Body, Identifier, Number, Structure, Template, Value};
use serde_json::{Map, Value as Json, json};

/// Must match the exact version pinned in Cargo.toml.
const HCL_RS_VERSION: &str = "0.19.8";

const USAGE: &str = "usage:
  hcl-rs-adapter capabilities
  hcl-rs-adapter parse <file.hcl>
  hcl-rs-adapter eval <file.hcl> [<context.json>]
  hcl-rs-adapter validate <file.hcl>";

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let result = match args.as_slice() {
        ["capabilities"] => Ok(capabilities()),
        ["parse", path] => parse(path),
        ["eval", path] => eval(path, None),
        ["eval", path, context] => eval(path, Some(context)),
        ["validate", path] => validate(path),
        _ => Err(USAGE.to_string()),
    };
    match result {
        Ok(output) => {
            println!("{output}");
            ExitCode::SUCCESS
        }
        Err(err) => {
            eprintln!("hcl-rs-adapter: {err}");
            ExitCode::FAILURE
        }
    }
}

fn capabilities() -> Json {
    json!({
        "implementation": "hcl-rs",
        "version": HCL_RS_VERSION,
        "operations": ["parse", "eval", "validate"],
        "features": ["functions"],
    })
}

enum Parsed {
    Body(Body),
    Invalid(Json),
}

fn parse_file(path: &str) -> Result<Parsed, String> {
    if path.ends_with(".hcl.json") {
        return Err("hcl-rs doesn't read the JSON syntax".into());
    }
    let bytes = fs::read(path).map_err(|err| format!("{path}: {err}"))?;
    // hcl-rs only accepts a &str, so invalid UTF-8 never reaches its parser.
    let Ok(source) = String::from_utf8(bytes) else {
        return Ok(Parsed::Invalid(invalid("parse", "input is not valid UTF-8")));
    };
    Ok(match hcl::parse(&source) {
        Ok(body) => Parsed::Body(body),
        Err(err) => Parsed::Invalid(invalid("parse", err)),
    })
}

/// Gives the answer `parse` gives. It converts every template the way `parse`
/// does, since that can fail (see `parse`), but builds no output, which for a
/// long traversal chain takes far longer than parsing.
fn validate(path: &str) -> Result<Json, String> {
    let body = match parse_file(path)? {
        Parsed::Body(body) => body,
        Parsed::Invalid(output) => return Ok(output),
    };
    Ok(match check_body(&body) {
        Ok(()) => json!({"valid": true}),
        Err(message) => invalid("parse", message),
    })
}

fn parse(path: &str) -> Result<Json, String> {
    let body = match parse_file(path)? {
        Parsed::Body(body) => body,
        Parsed::Invalid(output) => return Ok(output),
    };
    // hcl-rs keeps the text of each template and parses it again to convert it,
    // which fails for some templates its parser accepted, such as
    // "\u0025%{if true}x%{endif}". Those errors are parse errors too.
    Ok(match body_json(&body, &expr_json) {
        Ok(body) => json!({"valid": true, "body": body}),
        Err(message) => invalid("parse", message),
    })
}

fn eval(path: &str, context: Option<&str>) -> Result<Json, String> {
    let mut ctx = Context::new();
    if let Some(context) = context {
        declare_context(&mut ctx, context)?;
    }
    let body = match parse_file(path)? {
        Parsed::Body(body) => body,
        Parsed::Invalid(output) => return Ok(output),
    };
    // Templates that fail to convert are parse errors (see `parse`), even where
    // evaluation wouldn't reach them.
    if let Err(message) = check_body(&body) {
        return Ok(invalid("parse", message));
    }
    let evaluate = |expr: &Expression| {
        expr.evaluate(&ctx)
            .map(|value| value_json(&value))
            .map_err(|err| err.to_string())
    };
    Ok(match body_json(&body, &evaluate) {
        Ok(body) => json!({"valid": true, "body": body}),
        Err(message) => invalid("eval", message),
    })
}

fn invalid(phase: &str, message: impl ToString) -> Json {
    json!({"valid": false, "phase": phase, "errors": [{"message": message.to_string()}]})
}

/// Converts a body, using `convert` for attribute values. A body with a
/// repeated attribute name keeps the last value, since that's what hcl-rs
/// accepted.
fn body_json(body: &Body, convert: &dyn Fn(&Expression) -> Result<Json, String>) -> Result<Json, String> {
    let mut attributes = Map::new();
    let mut blocks = Vec::new();
    for structure in body.iter() {
        match structure {
            Structure::Attribute(attr) => {
                attributes.insert(attr.key.to_string(), convert(&attr.expr)?);
            }
            Structure::Block(block) => {
                let labels: Vec<&str> = block.labels.iter().map(|label| label.as_str()).collect();
                blocks.push(json!({
                    "type": block.identifier.as_str(),
                    "labels": labels,
                    "body": body_json(&block.body, convert)?,
                }));
            }
        }
    }
    Ok(json!({"attributes": attributes, "blocks": blocks}))
}

/// Converts the templates in a body and fails where `body_json` with
/// `expr_json` would, without building anything.
fn check_body(body: &Body) -> Result<(), String> {
    for structure in body.iter() {
        match structure {
            Structure::Attribute(attr) => check_expr(&attr.expr)?,
            Structure::Block(block) => check_body(&block.body)?,
        }
    }
    Ok(())
}

fn check_expr(expr: &Expression) -> Result<(), String> {
    match expr {
        Expression::Null | Expression::Bool(_) | Expression::Number(_) | Expression::String(_) => Ok(()),
        Expression::Variable(_) => Ok(()),
        Expression::Array(items) => items.iter().try_for_each(check_expr),
        Expression::Object(object) => object.iter().try_for_each(|(key, value)| {
            match key {
                ObjectKey::Identifier(_) => {}
                ObjectKey::Expression(expr) => check_expr(expr)?,
                #[allow(unreachable_patterns)]
                _ => return Err("unsupported object key".into()),
            }
            check_expr(value)
        }),
        Expression::TemplateExpr(template) => {
            let template = Template::from_expr(template).map_err(|err| err.to_string())?;
            check_elements(template.elements())
        }
        Expression::Traversal(traversal) => {
            check_expr(&traversal.expr)?;
            traversal.operators.iter().try_for_each(|operator| match operator {
                TraversalOperator::Index(key) => check_expr(key),
                TraversalOperator::GetAttr(_)
                | TraversalOperator::LegacyIndex(_)
                | TraversalOperator::FullSplat
                | TraversalOperator::AttrSplat => Ok(()),
                #[allow(unreachable_patterns)]
                _ => Err("unsupported traversal operator".into()),
            })
        }
        Expression::FuncCall(call) => call.args.iter().try_for_each(check_expr),
        Expression::Parenthesis(inner) => check_expr(inner),
        Expression::Conditional(cond) => {
            check_expr(&cond.cond_expr)?;
            check_expr(&cond.true_expr)?;
            check_expr(&cond.false_expr)
        }
        Expression::Operation(op) => match op.as_ref() {
            Operation::Unary(op) => check_expr(&op.expr),
            Operation::Binary(op) => {
                check_expr(&op.lhs_expr)?;
                check_expr(&op.rhs_expr)
            }
        },
        Expression::ForExpr(f) => {
            check_expr(&f.collection_expr)?;
            f.key_expr.as_ref().map(check_expr).transpose()?;
            check_expr(&f.value_expr)?;
            f.cond_expr.as_ref().map(check_expr).transpose()?;
            Ok(())
        }
        #[allow(unreachable_patterns)]
        _ => Err("unsupported expression".into()),
    }
}

fn check_elements(elements: &[Element]) -> Result<(), String> {
    elements.iter().try_for_each(|element| match element {
        Element::Literal(_) => Ok(()),
        Element::Interpolation(interp) => check_expr(&interp.expr),
        Element::Directive(directive) => match directive.as_ref() {
            Directive::If(dir) => {
                check_expr(&dir.cond_expr)?;
                check_elements(dir.true_template.elements())?;
                dir.false_template.as_ref().map(|template| check_elements(template.elements())).transpose()?;
                Ok(())
            }
            Directive::For(dir) => {
                check_expr(&dir.collection_expr)?;
                check_elements(dir.template.elements())
            }
        },
    })
}

fn literal(value: Json) -> Json {
    json!({"kind": "literal", "value": value})
}

fn expr_json(expr: &Expression) -> Result<Json, String> {
    Ok(match expr {
        Expression::Null => literal(json!({"null": "dynamic"})),
        Expression::Bool(b) => literal(json!({"bool": b})),
        Expression::Number(n) => literal(json!({"number": number_text(n)})),
        Expression::String(s) => literal(json!({"string": s})),
        Expression::Array(items) => json!({"kind": "tuple", "elements": exprs_json(items)?}),
        Expression::Object(object) => {
            let mut items = Vec::new();
            for (key, value) in object {
                let key = match key {
                    ObjectKey::Identifier(ident) => literal(json!({"string": ident.as_str()})),
                    ObjectKey::Expression(expr) => expr_json(expr)?,
                    _ => return Err("unsupported object key".into()),
                };
                items.push(json!({"key": key, "value": expr_json(value)?}));
            }
            json!({"kind": "object", "items": items})
        }
        Expression::TemplateExpr(template) => {
            let template = Template::from_expr(template).map_err(|err| err.to_string())?;
            json!({"kind": "template", "parts": elements_json(template.elements(), false, false)?})
        }
        Expression::Variable(var) => json!({"kind": "variable", "name": var.as_str()}),
        Expression::Traversal(traversal) => traversal_json(expr_json(&traversal.expr)?, &traversal.operators)?,
        Expression::FuncCall(call) => {
            let mut name: Vec<&str> = call.name.namespace.iter().map(|ident| ident.as_str()).collect();
            name.push(call.name.name.as_str());
            json!({
                "kind": "call",
                "name": name.join("::"),
                "args": exprs_json(&call.args)?,
                "expand_final": call.expand_final,
            })
        }
        Expression::Parenthesis(inner) => expr_json(inner)?,
        Expression::Conditional(cond) => json!({
            "kind": "conditional",
            "condition": expr_json(&cond.cond_expr)?,
            "true_result": expr_json(&cond.true_expr)?,
            "false_result": expr_json(&cond.false_expr)?,
        }),
        Expression::Operation(op) => match op.as_ref() {
            Operation::Unary(op) => json!({
                "kind": "unary",
                "operator": op.operator.as_str(),
                "operand": expr_json(&op.expr)?,
            }),
            Operation::Binary(op) => json!({
                "kind": "binary",
                "operator": op.operator.as_str(),
                "left": expr_json(&op.lhs_expr)?,
                "right": expr_json(&op.rhs_expr)?,
            }),
        },
        Expression::ForExpr(f) => json!({
            "kind": "for",
            "result": if f.key_expr.is_some() { "object" } else { "tuple" },
            "key_var": f.key_var.as_ref().map(|ident| ident.as_str()),
            "value_var": f.value_var.as_str(),
            "collection": expr_json(&f.collection_expr)?,
            "key": f.key_expr.as_ref().map(expr_json).transpose()?,
            "value": expr_json(&f.value_expr)?,
            "condition": f.cond_expr.as_ref().map(expr_json).transpose()?,
            "grouping": f.grouping,
        }),
        #[allow(unreachable_patterns)]
        _ => return Err("unsupported expression".into()),
    })
}

fn exprs_json(exprs: &[Expression]) -> Result<Vec<Json>, String> {
    exprs.iter().map(expr_json).collect()
}

/// Nests the traversal operators around `node`. A splat takes the operators
/// after it as its "each" expression: all of them for [*], but only the
/// attribute accesses for .*
fn traversal_json(mut node: Json, operators: &[TraversalOperator]) -> Result<Json, String> {
    let mut i = 0;
    while i < operators.len() {
        match &operators[i] {
            TraversalOperator::GetAttr(ident) => {
                node = json!({"kind": "get_attr", "object": node, "name": ident.as_str()});
            }
            TraversalOperator::Index(key) => {
                node = json!({"kind": "index", "collection": node, "key": expr_json(key)?});
            }
            TraversalOperator::LegacyIndex(index) => {
                let key = literal(json!({"number": index.to_string()}));
                node = json!({"kind": "index", "collection": node, "key": key});
            }
            TraversalOperator::FullSplat => {
                let each = traversal_json(json!({"kind": "splat_item"}), &operators[i + 1..])?;
                return Ok(json!({"kind": "splat", "source": node, "each": each}));
            }
            TraversalOperator::AttrSplat => {
                let rest = &operators[i + 1..];
                let attrs = rest.iter().take_while(|op| matches!(op, TraversalOperator::GetAttr(_))).count();
                let each = traversal_json(json!({"kind": "splat_item"}), &rest[..attrs])?;
                node = json!({"kind": "splat", "source": node, "each": each});
                i += attrs;
            }
            #[allow(unreachable_patterns)]
            _ => return Err("unsupported traversal operator".into()),
        }
        i += 1;
    }
    Ok(node)
}

/// Converts template elements. `strip_first` and `strip_last` say whether the
/// delimiters around these elements have strip markers facing them.
///
/// hcl-rs keeps strip markers as flags and applies them during evaluation, while
/// the protocol wants them applied. This follows hcl-rs's own evaluation
/// (strip_literal in src/eval/template.rs), so the output shows what hcl-rs
/// does. Heredoc indentation is already removed by hcl-rs's parser.
fn elements_json(elements: &[Element], strip_first: bool, strip_last: bool) -> Result<Vec<Json>, String> {
    let mut parts = Vec::new();
    for (i, element) in elements.iter().enumerate() {
        parts.push(match element {
            Element::Literal(text) => {
                let strip_start = if i == 0 { strip_first } else { strips(&elements[i - 1]).1 };
                let strip_end = if i + 1 == elements.len() { strip_last } else { strips(&elements[i + 1]).0 };
                literal(json!({"string": strip_literal(text, strip_start, strip_end)}))
            }
            Element::Interpolation(interp) => json!({"kind": "interpolation", "expr": expr_json(&interp.expr)?}),
            Element::Directive(directive) => match directive.as_ref() {
                Directive::If(dir) => {
                    let then_end = match &dir.false_template {
                        Some(_) => dir.else_strip.strip_start(),
                        None => dir.endif_strip.strip_start(),
                    };
                    json!({
                        "kind": "template_if",
                        "condition": expr_json(&dir.cond_expr)?,
                        "then": elements_json(dir.true_template.elements(), dir.if_strip.strip_end(), then_end)?,
                        "else": match &dir.false_template {
                            Some(template) => elements_json(
                                template.elements(),
                                dir.else_strip.strip_end(),
                                dir.endif_strip.strip_start(),
                            )?,
                            None => Vec::new(),
                        },
                    })
                }
                Directive::For(dir) => json!({
                    "kind": "template_for",
                    "key_var": dir.key_var.as_ref().map(|ident| ident.as_str()),
                    "value_var": dir.value_var.as_str(),
                    "collection": expr_json(&dir.collection_expr)?,
                    "body": elements_json(
                        dir.template.elements(),
                        dir.for_strip.strip_end(),
                        dir.endfor_strip.strip_start(),
                    )?,
                }),
            },
        });
    }
    Ok(parts)
}

/// Whether an element's opening and closing delimiters have strip markers.
fn strips(element: &Element) -> (bool, bool) {
    match element {
        Element::Literal(_) => (false, false),
        Element::Interpolation(interp) => (interp.strip.strip_start(), interp.strip.strip_end()),
        Element::Directive(directive) => match directive.as_ref() {
            Directive::If(dir) => (dir.if_strip.strip_start(), dir.endif_strip.strip_end()),
            Directive::For(dir) => (dir.for_strip.strip_start(), dir.endfor_strip.strip_end()),
        },
    }
}

/// The same stripping as hcl-rs's strip_literal: spaces, then at most one line break.
fn strip_literal(mut text: &str, strip_start: bool, strip_end: bool) -> &str {
    let is_space = |ch: char| ch.is_whitespace() && ch != '\r' && ch != '\n';
    if strip_start {
        text = text.trim_start_matches(is_space);
        text = text.strip_prefix("\r\n").or_else(|| text.strip_prefix('\n')).unwrap_or(text);
    }
    if strip_end {
        text = text.trim_end_matches(is_space);
        text = text.strip_suffix("\r\n").or_else(|| text.strip_suffix('\n')).unwrap_or(text);
    }
    text
}

/// hcl-rs values have no types beyond JSON's, so every array is reported as a
/// tuple and every null as a null of the dynamic type.
fn value_json(value: &Value) -> Json {
    match value {
        Value::Null => json!({"null": "dynamic"}),
        Value::Bool(b) => json!({"bool": b}),
        Value::Number(n) => json!({"number": number_text(n)}),
        Value::String(s) => json!({"string": s}),
        Value::Array(items) => json!({"tuple": items.iter().map(value_json).collect::<Vec<_>>()}),
        Value::Object(object) => {
            let attrs: Map<String, Json> = object.iter().map(|(k, v)| (k.clone(), value_json(v))).collect();
            json!({"object": attrs})
        }
    }
}

/// hcl-rs numbers print infinities and NaN as huge finite numbers, so report
/// them as what they are ("NaN" makes the runner flag the output).
fn number_text(n: &Number) -> String {
    match n.as_f64() {
        Some(f) if f.is_nan() => "NaN".to_string(),
        Some(f) if f.is_infinite() && f > 0.0 => "Infinity".to_string(),
        Some(f) if f.is_infinite() => "-Infinity".to_string(),
        _ => n.to_string(),
    }
}

fn declare_context(ctx: &mut Context, path: &str) -> Result<(), String> {
    let text = fs::read_to_string(path).map_err(|err| format!("{path}: {err}"))?;
    let context: Json = serde_json::from_str(&text).map_err(|err| format!("{path}: {err}"))?;
    let context = object_with_fields(&context, &["variables", "functions", "evaluation_mode"])
        .map_err(|err| format!("{path}: {err}"))?;
    // hcl-rs has no literal-only mode, but for the native syntax it is the same
    // as evaluating without variables and functions.
    if let Some(mode) = context.get("evaluation_mode")
        && (mode != "literal-only" || context.len() > 1)
    {
        return Err(format!("{path}: evaluation_mode must be literal-only, without variables or functions"));
    }
    let mut fixed_results = Vec::new();
    for (field, entries) in context.iter().filter(|(field, _)| field.as_str() != "evaluation_mode") {
        let entries = entries.as_object().ok_or_else(|| format!("{path}: {field} must be an object"))?;
        for (name, entry) in entries {
            // Names are used as given, without hcl-rs's sanitizing.
            if field == "variables" {
                let value = decode_value(entry).map_err(|err| format!("{path}: variable {name}: {err}"))?;
                ctx.declare_var(Identifier::unchecked(name.as_str()), value);
            } else {
                let func = func_def(entry, &mut fixed_results).map_err(|err| format!("{path}: function {name}: {err}"))?;
                ctx.declare_func(func_name(name), func);
            }
        }
    }
    FIXED_RESULTS.set(fixed_results).expect("the context is only read once");
    Ok(())
}

/// Returns the fields of a JSON object after checking that it has no fields
/// other than `allowed` and none of them is null.
fn object_with_fields<'a>(json: &'a Json, allowed: &[&str]) -> Result<&'a Map<String, Json>, String> {
    let object = json.as_object().ok_or_else(|| format!("expected an object, got {json}"))?;
    for (field, value) in object {
        if !allowed.contains(&field.as_str()) {
            return Err(format!("unknown field {field}"));
        }
        if value.is_null() {
            return Err(format!("field {field} is null"));
        }
    }
    Ok(object)
}

/// Splits a namespaced name such as ns::f into its parts.
fn func_name(name: &str) -> FuncName {
    let mut parts: Vec<Identifier> = name.split("::").map(Identifier::unchecked).collect();
    let last = parts.pop().expect("split returns at least one part");
    FuncName::new(last).with_namespace(parts)
}

/// Builds a function from a declaration. hcl-rs functions are plain fn
/// pointers, so a function with a fixed result gets one of the FIXED_RESULT_FUNCS,
/// which returns the result stored at its index.
fn func_def(decl: &Json, fixed_results: &mut Vec<Result<Value, String>>) -> Result<FuncDef, String> {
    let decl = object_with_fields(decl, &["params", "variadic_param", "result"])?;
    let mut builder = FuncDef::builder();
    if let Some(params) = decl.get("params") {
        let params = params.as_array().ok_or_else(|| format!("params must be a list, got {params}"))?;
        for param in params {
            builder = builder.param(param_type(param)?);
        }
    }
    if let Some(param) = decl.get("variadic_param") {
        builder = builder.variadic_param(param_type(param)?);
    }
    let func = match decl.get("result").ok_or("missing result")? {
        Json::String(s) if s == "arguments" => arguments as Func,
        result @ Json::Object(_) => {
            let result = object_with_fields(result, &["value", "error"])?;
            let func = *FIXED_RESULT_FUNCS
                .get(fixed_results.len())
                .ok_or("too many functions with fixed results")?;
            fixed_results.push(match (result.get("value"), result.get("error")) {
                (Some(value), None) => Ok(decode_value(value)?),
                (None, Some(Json::String(message))) => Err(message.clone()),
                _ => return Err(format!("invalid result {}", Json::Object(result.clone()))),
            });
            func
        }
        result => return Err(format!("invalid result {result}")),
    };
    Ok(builder.build(func))
}

fn arguments(args: FuncArgs) -> Result<Value, String> {
    Ok(Value::Array(args.into_values()))
}

static FIXED_RESULTS: OnceLock<Vec<Result<Value, String>>> = OnceLock::new();

macro_rules! fixed_result_funcs {
    ($($name:ident = $index:literal),* $(,)?) => {
        $(
            fn $name(_: FuncArgs) -> Result<Value, String> {
                FIXED_RESULTS.get().expect("results are stored before evaluation")[$index].clone()
            }
        )*
        const FIXED_RESULT_FUNCS: &[Func] = &[$($name),*];
    };
}

fixed_result_funcs!(fixed0 = 0, fixed1 = 1, fixed2 = 2, fixed3 = 3, fixed4 = 4, fixed5 = 5, fixed6 = 6, fixed7 = 7);

/// hcl-rs parameter types have no unknown or dynamic values to allow, and no
/// tuple, object, list, set or map types, only arrays and objects whose
/// elements all have one type.
fn param_type(param: &Json) -> Result<ParamType, String> {
    let param = object_with_fields(param, &["name", "type", "allow_null", "allow_unknown", "allow_dynamic_type"])?;
    for flag in ["allow_null", "allow_unknown", "allow_dynamic_type"] {
        if param.get(flag).is_some_and(|value| !value.is_boolean()) {
            return Err(format!("{flag} must be true or false"));
        }
    }
    if param.get("name").is_some_and(|name| !name.is_string()) {
        return Err("a parameter name must be a string".into());
    }
    let ty = param.get("type").ok_or("parameter without a type")?;
    let allow_null = param.get("allow_null").and_then(Json::as_bool).unwrap_or(false);
    let ty = match ty.as_str() {
        Some("string") => ParamType::String,
        Some("number") => ParamType::Number,
        Some("bool") => ParamType::Bool,
        Some("dynamic") if allow_null => return Ok(ParamType::Any),
        Some("dynamic") => {
            // Any also accepts null, so list the non-null kinds instead.
            let any = || Box::new(ParamType::Any);
            return Ok(ParamType::OneOf(vec![
                ParamType::Bool,
                ParamType::Number,
                ParamType::String,
                ParamType::Array(any()),
                ParamType::Object(any()),
            ]));
        }
        _ => return Err(format!("hcl-rs has no parameter type for {ty}")),
    };
    Ok(if allow_null { ParamType::Nullable(Box::new(ty)) } else { ty })
}

/// Decodes a value in the protocol's encoding: an object with one key naming the
/// kind of value, and an element_type for lists, sets and maps. hcl-rs values
/// have no types, so types are checked for presence but otherwise ignored.
fn decode_value(value: &Json) -> Result<Value, String> {
    let not_a_value = || format!("not a value: {value}");
    let node = value.as_object().ok_or_else(not_a_value)?;
    let kinds: Vec<(&String, &Json)> = node.iter().filter(|(key, _)| key.as_str() != "element_type").collect();
    let [(kind, x)] = kinds.as_slice() else {
        return Err(not_a_value());
    };
    if x.is_null() || node.contains_key("element_type") != matches!(kind.as_str(), "list" | "set" | "map") {
        return Err(not_a_value());
    }
    let malformed = || format!("malformed {kind} value: {value}");
    Ok(match kind.as_str() {
        "string" => Value::String(x.as_str().ok_or_else(malformed)?.to_owned()),
        "number" => Value::Number(decode_number(x.as_str().ok_or_else(malformed)?)?),
        "bool" => Value::Bool(x.as_bool().ok_or_else(malformed)?),
        "null" => Value::Null,
        "tuple" | "list" | "set" => {
            let items = x.as_array().ok_or_else(malformed)?;
            Value::Array(items.iter().map(decode_value).collect::<Result<_, _>>()?)
        }
        "object" | "map" => {
            let items = x.as_object().ok_or_else(malformed)?;
            Value::Object(
                items
                    .iter()
                    .map(|(k, v)| Ok((k.clone(), decode_value(v)?)))
                    .collect::<Result<_, String>>()?,
            )
        }
        "unknown" => return Err("hcl-rs has no unknown values".into()),
        _ => return Err(format!("not a value: {value}")),
    })
}

fn decode_number(text: &str) -> Result<Number, String> {
    if let Ok(n) = text.parse::<i64>() {
        return Ok(Number::from(n));
    }
    if let Ok(n) = text.parse::<u64>() {
        return Ok(Number::from(n));
    }
    text.parse::<f64>()
        .ok()
        .and_then(Number::from_f64)
        .ok_or_else(|| format!("hcl-rs cannot represent the number {text}"))
}
