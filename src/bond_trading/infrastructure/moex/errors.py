class MoexError(RuntimeError):
    pass


class MoexNotFoundError(MoexError):
    pass


class MoexDataError(MoexError):
    pass


class MoexTemporaryError(MoexError):
    pass
