# Second warmup-bound workload, structurally different from wl_many: a small
# bytecode interpreter whose dispatch loop calls many distinct handler
# functions (the shape of real VMs / parsers / template engines on cold start).
# The dispatch loop runs just past PyPy's trace threshold, so it compiles and
# inlines the handlers it sees -- expensive to trace, barely amortized. Run the
# whole interpreter once per "program", over several distinct programs.
import sys

NOP, ADD, SUB, MUL, XOR, SHR, SHL, ORR, AND, INC, DEC, NEG, DUP, ROT, MOD = range(15)


def make_handlers(tag):
    src = []
    ops = [("ADD", "a + b"), ("SUB", "a - b"), ("MUL", "(a * b) & 0xffffff"),
           ("XOR", "a ^ b"), ("SHR", "a >> (b & 7)"), ("SHL", "(a << (b & 7)) & 0xffffff"),
           ("ORR", "a | b"), ("AND", "a & b"), ("INC", "a + 1"), ("DEC", "a - 1"),
           ("NEG", "(-a) & 0xffffff"), ("DUP", "a + a"), ("ROT", "(a >> 3) | (a << 3)"),
           ("MOD", "a % (b | 1)")]
    for name, expr in ops:
        src.append("def h_%s_%s(a, b):\n    return %s\n" % (name, tag, expr))
    ns = {}
    exec("".join(src), ns)
    return [ns["h_%s_%s" % (n, tag)] for n, _ in ops]


def run_program(handlers, code, n):
    acc = 0
    b = 7
    i = 0
    while i < n:
        h = handlers[code[i % len(code)]]
        acc = h(acc, b) & 0xffffff
        b = (b + 1) & 7
        i += 1
    return acc


def main(progs, n):
    code = [0, 3, 5, 1, 7, 2, 11, 4, 9, 6, 13, 8, 12, 10]
    total = 0
    for p in range(progs):
        handlers = make_handlers(str(p))
        total = (total + run_program(handlers, code, n)) & 0xffffff
    return total


if __name__ == '__main__':
    progs = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 1300
    print main(progs, n)
