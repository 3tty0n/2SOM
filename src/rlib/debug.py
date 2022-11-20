try:
    from rpython.rlib.debug import make_sure_not_resized  # pylint: disable=W
    from rpython.rlib.debug import debug_print  # pylint: disable=W
except ImportError:
    "NOT_RPYTHON"

    def make_sure_not_resized(_):
        pass

    def debug_print(_):
        pass
