class ColetaErro(RuntimeError):
    """Falha da rodada que não é um pedido da fonte para parar."""


class OruloError(ColetaErro):
    pass


class OruloBlocked(OruloError):
    """429 ou 403: a fonte pediu para parar. A coleta fecha incompleta."""


class OruloAuthError(OruloError):
    """Login falhou, ou 401 persistiu depois de refazer o login uma vez."""
