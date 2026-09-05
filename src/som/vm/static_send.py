from som.interpreter.ast.nodes.dispatch import CachedDispatchNode
from som.interpreter.bc.bytecodes import Bytecodes
from som.placement import send_place_is_aot


def _q_send_for(bytecode):
    if bytecode == Bytecodes.send_1:
        return Bytecodes.q_super_send_1
    if bytecode == Bytecodes.send_2:
        return Bytecodes.q_super_send_2
    if bytecode == Bytecodes.send_3:
        return Bytecodes.q_super_send_3
    if bytecode == Bytecodes.send_n:
        return Bytecodes.q_super_send_n
    return Bytecodes.invalid


def _plain_send_for(bytecode):
    if bytecode == Bytecodes.q_super_send_1:
        return Bytecodes.send_1
    if bytecode == Bytecodes.q_super_send_2:
        return Bytecodes.send_2
    if bytecode == Bytecodes.q_super_send_3:
        return Bytecodes.send_3
    if bytecode == Bytecodes.q_super_send_n:
        return Bytecodes.send_n
    return Bytecodes.invalid


def _is_subclass(clazz, ancestor):
    c = clazz
    while True:
        if c is ancestor:
            return True
        if not c.has_super_class():
            return False
        c = c.get_super_class()


class _Site(object):
    def __init__(self, method, idx):
        self.method = method
        self.idx = idx


class StaticSendBinder(object):
    def __init__(self):
        self.classes = []
        self.sites = {}
        self.candidates = 0
        self.bound = 0
        self.unbound = 0

    def class_loaded(self, universe, clazz):
        if not send_place_is_aot() or clazz is None:
            return
        metaclass = clazz.get_class(universe)
        self._invalidate_overridden(clazz)
        self._invalidate_overridden(metaclass)
        self.classes.append(clazz)
        self.classes.append(metaclass)
        self._bind_class(clazz)
        self._bind_class(metaclass)

    def _own_invokables(self, clazz):
        result = []
        for inv in clazz.get_instance_invokables_for_disassembler():
            if inv.get_holder() is clazz:
                result.append(inv)
        return result

    def _invalidate_overridden(self, clazz):
        for inv in self._own_invokables(clazz):
            sites = self.sites.get(inv.get_signature(), None)
            if sites is None:
                continue
            for site in sites:
                holder = site.method.get_holder()
                if holder is not clazz and _is_subclass(clazz, holder):
                    self._unbind(site)

    def _unbind(self, site):
        method = site.method
        plain = _plain_send_for(method.get_bytecode(site.idx))
        if plain == Bytecodes.invalid:
            return
        method.set_bytecode(site.idx, plain)
        method.set_inline_cache(site.idx, None)
        self.unbound += 1

    def _overridden_below(self, clazz, selector):
        for d in self.classes:
            if d is clazz or not _is_subclass(d, clazz):
                continue
            if d.lookup_own_invokable(selector) is not None:
                return True
        return False

    def _bind_class(self, clazz):
        for inv in self._own_invokables(clazz):
            self._bind_method(inv, clazz)

    def _bind_method(self, method, clazz):
        from som.vmobjects.method_bc import BcAbstractMethod

        if not isinstance(method, BcAbstractMethod):
            return
        for lit in method.get_literals():
            if lit.is_invokable():
                self._bind_method(lit, clazz)
        for idx in method.get_self_send_sites():
            self.candidates += 1
            bytecode = method.get_bytecode(idx)
            quick = _q_send_for(bytecode)
            if quick == Bytecodes.invalid:
                continue
            selector = method.get_constant(idx)
            target = clazz.lookup_invokable(selector)
            if target is None or self._overridden_below(clazz, selector):
                continue
            method.set_inline_cache(idx, CachedDispatchNode(None, target, None))
            method.set_bytecode(idx, quick)
            sites = self.sites.get(selector, None)
            if sites is None:
                sites = []
                self.sites[selector] = sites
            sites.append(_Site(method, idx))
            self.bound += 1

    def stats(self):
        return "static-send: candidates=%d bound=%d unbound=%d" % (
            self.candidates, self.bound, self.unbound)
