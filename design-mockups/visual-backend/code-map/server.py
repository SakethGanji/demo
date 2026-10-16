"""Local code-map prototype. Parses Python; never imports or executes submitted source."""
import argparse
import ast
import builtins
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLE = HERE.parents[2] / 'examples/banking-operations/banking_ops.py'
MAX_SOURCE = 256 * 1024


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted(node.value)
        return parent + '.' + node.attr if parent else None
    return None


def analyze(source):
    revision = hashlib.sha256(source.encode()).hexdigest()
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return {'source_hash': revision, 'valid': False, 'error': {
            'message': error.msg, 'line': error.lineno, 'column': error.offset}}
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    names = {node.name for node in functions}
    global_assignments = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            global_assignments.update(child.id for child in ast.walk(node)
                                      if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store))
    modules = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.update({alias.asname or alias.name: alias.name for alias in node.names})
    api_aliases = {alias.asname or alias.name for node in tree.body
                   if isinstance(node, ast.ImportFrom) and node.module == 'studio'
                   for alias in node.names if alias.name == 'api'}
    result, edges = [], []
    for function in functions:
        args = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
        if function.args.vararg:
            args.append(function.args.vararg)
        if function.args.kwarg:
            args.append(function.args.kwarg)
        # Lexical locals can shadow top-level functions and imported module names.
        locals_ = {arg.arg for arg in args}
        nested_scopes = []
        calls = []
        resources = set()
        controls = []
        routes = []
        for decorator in function.decorator_list:
            decorator_name = dotted(decorator.func) if isinstance(decorator, ast.Call) else None
            root = decorator_name.split('.')[0] if decorator_name else None
            if decorator_name and root in api_aliases and root not in global_assignments and decorator_name.split('.')[-1] in {'get', 'post', 'put', 'patch', 'delete'}:
                path = decorator.args[0] if decorator.args else None
                if isinstance(path, ast.Constant) and isinstance(path.value, str):
                    routes.append({'method': decorator.func.attr.upper(), 'path': path.value})
        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                nested_scopes.append({'line': node.lineno, 'kind': 'Nested function', 'name': node.name})
                locals_.add(node.name)
            visit_AsyncFunctionDef = visit_FunctionDef
            def visit_ClassDef(self, node):
                nested_scopes.append({'line': node.lineno, 'kind': 'Nested class', 'name': node.name})
                locals_.add(node.name)
            def visit_Name(self, node):
                if isinstance(node.ctx, ast.Store):
                    locals_.add(node.id)
            def visit_Import(self, node):
                locals_.update(alias.asname or alias.name.split('.')[0] for alias in node.names)
            def visit_ImportFrom(self, node):
                locals_.update(alias.asname or alias.name for alias in node.names)
            def visit_Call(self, node):
                calls.append(node)
                self.generic_visit(node)
            def generic_visit(self, node):
                if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)):
                    controls.append({'kind': type(node).__name__, 'line': node.lineno})
                super().generic_visit(node)
        visitor = Visitor()
        for statement in function.body:
            visitor.visit(statement)
        mapped = []
        for call in sorted(calls, key=lambda node: (node.lineno, node.col_offset)):
            name = dotted(call.func)
            root = name.split('.')[0] if name else None
            info = {'id': f'{function.name}:{call.lineno}:{call.col_offset}',
                    'line': call.lineno, 'end_line': call.end_lineno,
                    'label': name or 'Dynamic call', 'kind': 'custom', 'target': None}
            if isinstance(call.func, ast.Name) and name in names and name not in locals_ and name not in global_assignments:
                info.update(kind='local', target=name)
                edges.append({'from': function.name, 'to': name, 'call_id': info['id'], 'line': call.lineno})
            elif root not in locals_ and root not in global_assignments and modules.get(root) == 'studio' and name and name.startswith(root + '.nodes.'):
                info.update(kind='connector', label=name.split('.nodes.', 1)[1], effects='Not established by syntax analysis')
            elif root not in locals_ and root not in global_assignments and modules.get(root) == 'studio' and name == root + '.secret':
                info['kind'] = 'secret'
                if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    resources.add(call.args[0].value)
                    info['reference'] = call.args[0].value
                else:
                    info['reference'] = 'Dynamic reference'
            elif isinstance(call.func, ast.Name) and name in vars(builtins) and name not in {'eval', 'exec', 'open', '__import__'} and name not in locals_:
                info['kind'] = 'builtin'
            elif isinstance(call.func, ast.Attribute) and root and root not in modules:
                info['kind'] = 'method'
            mapped.append(info)
        visible = [call for call in mapped if call['kind'] in {'local', 'connector', 'custom'}]
        result.append({'id': function.name, 'name': function.name,
                       'label': function.name.strip('_').replace('_', ' ').capitalize(),
                       'line': function.lineno, 'end_line': function.end_lineno,
                       'async': isinstance(function, ast.AsyncFunctionDef),
                       'parameters': [arg.arg for arg in args], 'routes': routes,
                       'calls': visible, 'secret_references': sorted(resources),
                       'controls': controls, 'custom_scopes': nested_scopes,
                       'other_call_count': len(mapped) - len(visible)})
    return {'source_hash': revision, 'valid': True, 'functions': result, 'edges': edges,
            'analysis': 'Conservative syntax projection. Calls show dependencies, not a proven execution path. Dynamic dispatch, aliases and external behavior may be unresolved.'}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/source':
            source = EXAMPLE.read_text()
            return self.send_json({'name': EXAMPLE.name, 'source': source, 'model': analyze(source)})
        files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
        if self.path not in files:
            return self.send_json({'error': 'Not found'}, 404)
        filename, mime = files[self.path]
        body = (HERE / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'none'; base-uri 'none'; form-action 'none'")
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != '/api/analyze':
            return self.send_json({'error': 'Not found'}, 404)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 1 or size > MAX_SOURCE:
                return self.send_json({'error': 'Source exceeds prototype request limit'}, 413)
            data = json.loads(self.rfile.read(size))
            source = data.get('source')
            if not isinstance(source, str):
                raise ValueError('source must be a string')
        except (ValueError, UnicodeError, AttributeError):
            return self.send_json({'error': 'Expected JSON with a source string'}, 400)
        return self.send_json(analyze(source))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8770)
    args = parser.parse_args()
    print(f'Code-map prototype on loopback port {args.port}; submitted code is never executed.', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
