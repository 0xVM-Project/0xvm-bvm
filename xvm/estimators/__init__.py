import os
from typing import (
    Callable,
    cast,
)

from xvm.abc import (
    SignedTransactionAPI,
    StateAPI,
)
from xvm._utils.module_loading import (
    import_string,
)


def get_gas_estimator() -> Callable[[StateAPI, SignedTransactionAPI], int]:
    import_path = os.environ.get(
        "GAS_ESTIMATOR_BACKEND_FUNC",
        "xvm.estimators.gas.binary_gas_search_intrinsic_tolerance",
    )
    return cast(
        Callable[[StateAPI, SignedTransactionAPI], int], import_string(import_path)
    )
