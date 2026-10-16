"""Conservative single-module source projection. Never imports or executes analyzed code.

No model, database or secret lookup is involved. Dynamic dispatch, external methods
and nested scopes are not proven execution paths; expose those limits to callers.
"""
import ast
import builtins
import hashlib


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted(node.value)
        return parent + '.' + node.attr if parent else None
    return None


def analyze_code_map(source: str) -> dict:
    revision = hashlib.sha256(source.encode()).hexdigest()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as error:
        return {'source_hash': revision, 'valid': False, 'error': {
            'message': getattr(error, 'msg', 'Source cannot be parsed'), 'line': getattr(error, 'lineno', None), 'column': getattr(error, 'offset', None)}}
    if sum(1 for _ in ast.walk(tree)) > 20_000:
        return {'source_hash': revision, 'valid': False, 'error': {'message': 'Module is too complex to map (20,000 syntax nodes maximum)', 'line': None, 'column': None}}
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    names = {node.name for node in functions}
    if len(functions) > 200 or len(names) != len(functions):
        return {'source_hash': revision, 'valid': False, 'error': {
            'message': 'Map supports at most 200 uniquely named top-level functions', 'line': None, 'column': None}}

    global_assignments = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            global_assignments.update(child.id for child in ast.walk(node)
                                      if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store))
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            global_assignments.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            global_assignments.update(alias.asname or alias.name.split('.')[0] for alias in node.names
                                      if (alias.asname or alias.name.split('.')[0]) in names)
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
            if decorator_name and root in api_aliases and root not in names and root not in global_assignments and decorator_name.split('.')[-1] in {'get', 'post', 'put', 'patch', 'delete'}:
                path = decorator.args[0] if decorator.args else None
                if isinstance(path, ast.Constant) and isinstance(path.value, str):
                    routes.append({'method': decorator.func.attr.upper(), 'path': path.value})
        class Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                nested_scopes.append({'line': node.lineno, 'kind': 'Nested function', 'name': node.name})
                locals_.add(node.name)
            visit_AsyncFunctionDef = visit_FunctionDef
            def visit_Lambda(self, node):
                nested_scopes.append({'line': node.lineno, 'kind': 'Lambda', 'name': '<lambda>'})
            def visit_ExceptHandler(self, node):
                if node.name:
                    locals_.add(node.name)
                self.generic_visit(node)
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
            elif root not in names and root not in locals_ and root not in global_assignments and modules.get(root) == 'studio' and name and name.startswith(root + '.nodes.'):
                info.update(kind='connector', label=name.split('.nodes.', 1)[1], effects='Not established by syntax analysis')
            elif root not in names and root not in locals_ and root not in global_assignments and modules.get(root) == 'studio' and name == root + '.secret':
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
