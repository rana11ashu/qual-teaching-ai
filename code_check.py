import ast
import logging

logger = logging.getLogger(__name__)

# Block imports/builtins that can be used to perform malicious actions or access sensitive resources.
# this can be adjusted accoding to security standards.
BLOCKED_IMPORTS = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib",
    "importlib", "ctypes", "signal", "multiprocessing", "threading",
    "pty", "tty", "termios", "fcntl", "mmap", "resource",
}

BLOCKED_BUILTINS = {
    "eval", "exec", "compile", "__import__", "open",
    "breakpoint", "input",
}


class SecurityVisitor(ast.NodeVisitor):
    '''AST visitor that checks for blocked imports and built-in function calls.'''
    def __init__(self):
        self.violations = []

    def visit_Import(self, node):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in BLOCKED_IMPORTS:
                self.violations.append(f"blocked import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        root = (node.module or "").split(".")[0]
        if root in BLOCKED_IMPORTS:
            self.violations.append(f"blocked import: {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node):
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr

        if name in BLOCKED_BUILTINS:
            self.violations.append(f"blocked call: {name}()")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id in BLOCKED_IMPORTS:
            self.violations.append(f"blocked attribute access: {node.value.id}.{node.attr}")
        self.generic_visit(node)


def validate_code(code: str) -> tuple[bool, str]:
    '''Validate the code for syntax errors and security violations.'''
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        logger.warning(f"syntax error: {e}")
        return False, f"syntax error: {e}"

    visitor = SecurityVisitor()
    visitor.visit(tree)

    if visitor.violations:
        for v in visitor.violations:
            logger.warning(f"code rejected — {v}")
        return False, visitor.violations[0]

    return True, ""