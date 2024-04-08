import os
from pathlib import Path

#
# Filesystem path utils
#
def get_local_data_dir(chain_name: str, xvm_root_dir: Path) -> Path:
    """
    Returns the base directory path where data for a given chain will be stored.
    """
    try:
        return Path(os.environ['XVM_DATA_DIR'])
    except KeyError:
        return xvm_root_dir / chain_name

NODEKEY_FILENAME = 'nodekey'
