from som.interpreter.ast.nodes.dispatch import (
    CachedDispatchNode,
    GenericDispatchNode,
    TrivialFieldReadNode,
    TrivialFieldWriteNode,
    TrivialLiteralNode,
)
from som.interpreter.bc.bytecodes import Bytecodes, compute_send_ordinals
from som.placement import send_place_is_aot, send_place_is_pgo
from som.vm import pgo, send_inline
from som.vmobjects.method_trivial import FieldRead, FieldWrite, LiteralReturn


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
    if bytecode == Bytecodes.q_self_literal or bytecode == Bytecodes.q_self_field_read:
        return Bytecodes.send_1
    if bytecode == Bytecodes.q_self_field_write:
        return Bytecodes.send_2
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
        self.inlined = 0
        self.generic_sites = 0

    def class_loaded(self, universe, clazz):
        if clazz is None or not (send_place_is_aot() or send_place_is_pgo()):
            return
        metaclass = clazz.get_class(universe)
        self._invalidate_overridden(clazz)
        self._invalidate_overridden(metaclass)
        self.classes.append(clazz)
        self.classes.append(metaclass)
        if send_place_is_pgo():
            self._mark_generic_class(clazz, universe)
            self._mark_generic_class(metaclass, universe)
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
            if method.get_inline_cache(idx) is not None:
                continue
            selector = method.get_constant(idx)
            target = clazz.lookup_invokable(selector)
            if target is None or self._overridden_below(clazz, selector):
                continue
            if send_inline.is_enabled() and self._try_inline_trivial(
                method, idx, bytecode, target
            ):
                self.inlined += 1
            else:
                method.set_inline_cache(idx, CachedDispatchNode(None, target, None))
                method.set_bytecode(idx, quick)
            sites = self.sites.get(selector, None)
            if sites is None:
                sites = []
                self.sites[selector] = sites
            sites.append(_Site(method, idx))
            self.bound += 1

    def _try_inline_trivial(self, method, idx, bytecode, target):
        if isinstance(target, LiteralReturn):
            if bytecode != Bytecodes.send_1:
                return False
            method.set_inline_cache(idx, TrivialLiteralNode(target._value))
            method.set_bytecode(idx, Bytecodes.q_self_literal)
            return True
        if isinstance(target, FieldRead):
            if bytecode != Bytecodes.send_1 or target._context_level != 0:
                return False
            method.set_inline_cache(idx, TrivialFieldReadNode(target._field_idx))
            method.set_bytecode(idx, Bytecodes.q_self_field_read)
            return True
        if isinstance(target, FieldWrite):
            if bytecode != Bytecodes.send_2:
                return False
            method.set_inline_cache(idx, TrivialFieldWriteNode(target._field_idx))
            method.set_bytecode(idx, Bytecodes.q_self_field_write)
            return True
        return False

    def _mark_generic_class(self, clazz, universe):
        for inv in self._own_invokables(clazz):
            self._mark_generic_method(inv, universe)

    def _mark_generic_method(self, method, universe):
        from som.vmobjects.method_bc import BcAbstractMethod

        if not isinstance(method, BcAbstractMethod):
            return
        for lit in method.get_literals():
            if lit.is_invokable():
                self._mark_generic_method(lit, universe)
        holder = method.get_holder()
        if holder is None:
            return
        holder_name = holder.get_name().get_embedded_string()
        sig_name = method.get_signature().get_embedded_string()
        for idx, ordinal in compute_send_ordinals(method).items():
            selector = method.get_constant(idx)
            selector_name = selector.get_embedded_string()
            if pgo.is_marked_generic(holder_name, sig_name, selector_name, ordinal):
                if method.get_inline_cache(idx) is None:
                    method.set_inline_cache(idx, GenericDispatchNode(selector, universe))
                    self.generic_sites += 1

    def stats(self):
        result = "static-send: candidates=%d bound=%d unbound=%d inlined_sites=%d" % (
            self.candidates, self.bound, self.unbound, self.inlined)
        if send_place_is_pgo():
            result += "\npgo: generic_sites=%d" % self.generic_sites
        return result
