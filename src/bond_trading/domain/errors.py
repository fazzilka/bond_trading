class DomainError(ValueError):
    code = "domain_error"

    def __init__(self, message: str, details: object = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class InvalidHoldingPeriodError(DomainError):
    code = "invalid_holding_period"


class InvalidAmountError(DomainError):
    code = "invalid_amount"


class InvalidIsinError(DomainError):
    code = "invalid_isin"
