import logging
import operator
import random
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Sequence,
    Tuple,
    Type,
)

from eth_typing import (
    Address,
    BlockNumber,
    Hash32,
)
from eth_utils import (
    encode_hex,
)
from eth_utils.toolz import (
    concatv,
    sliding_window,
)

from xvm._utils.datatypes import (
    Configurable,
)
from xvm._utils.db import (
    apply_state_dict,
)
from xvm._utils.rlp import (
    validate_imported_block_unchanged,
)
from xvm._warnings import (
    catch_and_ignore_import_warning,
)
from xvm.abc import (
    AtomicDatabaseAPI,
    BlockAndMetaWitness,
    BlockAPI,
    BlockHeaderAPI,
    BlockImportResult,
    BlockPersistResult,
    ChainAPI,
    ChainDatabaseAPI,
    ComputationAPI,
    MiningChainAPI,
    ReceiptAPI,
    SignedTransactionAPI,
    StateAPI,
    VirtualMachineAPI,
)
from xvm.constants import (
    EMPTY_UNCLE_HASH,
    MAX_UNCLE_DEPTH,
)
from xvm.db.chain import (
    ChainDB,
)
from xvm.db.header import (
    HeaderDB,
)
from xvm.estimators import (
    get_gas_estimator,
)
from xvm.exceptions import (
    HeaderNotFound,
    TransactionNotFound,
    VMNotFound,
)
from xvm.rlp.headers import (
    BlockHeader,
)
from xvm.typing import (
    AccountState,
    HeaderParams,
    StaticMethod,
)
from xvm.validation import (
    validate_block_number,
    validate_uint256,
    validate_vm_configuration,
    validate_word,
)
from xvm.vm.chain_context import (
    ChainContext,
)

with catch_and_ignore_import_warning():
    from eth_utils import (
        ValidationError,
        to_set,
    )
    from eth_utils.toolz import (
        assoc,
        compose,
        groupby,
        iterate,
        take,
    )


class BaseChain(Configurable, ChainAPI):
    """
    The base class for all Chain objects
    """

    chaindb: ChainDatabaseAPI = None
    chaindb_class: Type[ChainDatabaseAPI] = None
    # consensus_context_class: Type[ConsensusContextAPI] = None
    vm_configuration: Tuple[Tuple[BlockNumber, Type[VirtualMachineAPI]], ...] = None
    chain_id: int = None

    @classmethod
    def get_vm_class_for_block_number(
        cls, block_number: BlockNumber
    ) -> Type[VirtualMachineAPI]:
        if cls.vm_configuration is None:
            raise AttributeError(
                "Chain classes must define the VMs in vm_configuration"
            )

        validate_block_number(block_number)
        for start_block, vm_class in reversed(cls.vm_configuration):
            if block_number >= start_block:
                return vm_class
        else:
            raise VMNotFound(f"No vm available for block #{block_number}")

    @classmethod
    def get_vm_class(cls, header: BlockHeaderAPI) -> Type[VirtualMachineAPI]:
        return cls.get_vm_class_for_block_number(header.block_number)

    #
    # Validation API
    #
    def validate_chain(
        self,
        root: BlockHeaderAPI,
        descendants: Tuple[BlockHeaderAPI, ...],
        seal_check_random_sample_rate: int = 1,
    ) -> None:
        all_indices = range(len(descendants))
        if seal_check_random_sample_rate == 1:
            indices_to_check_seal = set(all_indices)
        elif seal_check_random_sample_rate == 0:
            indices_to_check_seal = set()
        else:
            sample_size = len(all_indices) // seal_check_random_sample_rate
            indices_to_check_seal = set(random.sample(all_indices, sample_size))

        header_pairs = sliding_window(2, concatv([root], descendants))

        for index, (parent, child) in enumerate(header_pairs):
            if child.parent_hash != parent.hash:
                raise ValidationError(
                    f"Invalid header chain; {child} has parent"
                    f" {encode_hex(child.parent_hash)},"
                    f" but expected {encode_hex(parent.hash)}"
                )
            vm = self.get_vm(child)
            try:
                vm.validate_header(child, parent)
            except ValidationError as exc:
                raise ValidationError(
                    f"{child} is not a valid child of {parent}: {exc}"
                ) from exc

            if index in indices_to_check_seal:
                vm.validate_seal(child)

    def validate_chain_extension(self, headers: Tuple[BlockHeaderAPI, ...]) -> None:
        for index, header in enumerate(headers):
            vm = self.get_vm(header)

            # pass in any parents that are not already in the database
            parents = headers[:index]
            # TODO: XVM/ we don't have other extension here currently.
            pass

class Chain(BaseChain):
    logger = logging.getLogger("xvm.chain.chain.Chain")
    gas_estimator: StaticMethod[Callable[[StateAPI, SignedTransactionAPI], int]] = None

    chaindb_class: Type[ChainDatabaseAPI] = ChainDB

    def __init__(self, base_db: AtomicDatabaseAPI) -> None:
        if not self.vm_configuration:
            raise ValueError(
                "The Chain class cannot be instantiated with "
                "an empty `vm_configuration`"
            )
        else:
            validate_vm_configuration(self.vm_configuration)

        self.chaindb = self.get_chaindb_class()(base_db)
        # self.consensus_context = self.consensus_context_class(self.chaindb.db)
        self.headerdb = HeaderDB(base_db)
        if self.gas_estimator is None:
            self.gas_estimator = get_gas_estimator()

    #
    # Helpers
    #
    @classmethod
    def get_chaindb_class(cls) -> Type[ChainDatabaseAPI]:
        if cls.chaindb_class is None:
            raise AttributeError("`chaindb_class` not set")
        return cls.chaindb_class

    #
    # Chain API
    #
    @classmethod
    def from_genesis(
        cls,
        base_db: AtomicDatabaseAPI,
        genesis_params: Dict[str, HeaderParams],
        genesis_state: AccountState = None,
    ) -> "BaseChain":
        genesis_vm_class = cls.get_vm_class_for_block_number(BlockNumber(0))

        pre_genesis_header = BlockHeader(difficulty=0, block_number=-1, gas_limit=0)
        chain_context = ChainContext(cls.chain_id)
        state = genesis_vm_class.build_state(base_db, pre_genesis_header, chain_context)

        if genesis_state is None:
            genesis_state = {}

        # mutation
        apply_state_dict(state, genesis_state)
        state.persist()

        if "state_root" not in genesis_params:
            # If the genesis state_root was not specified, use the value
            # computed from the initialized state database.
            genesis_params = assoc(genesis_params, "state_root", state.state_root)
        elif genesis_params["state_root"] != state.state_root:
            # If the genesis state_root was specified, validate that it matches
            # the computed state from the initialized state database.
            raise ValidationError(
                "The provided genesis state root does not match the computed "
                f"genesis state root.  Got {state.state_root!r}.  "
                f"Expected {genesis_params['state_root']!r}"
            )

        genesis_header = genesis_vm_class.create_genesis_header(**genesis_params)
        return cls.from_genesis_header(base_db, genesis_header)

    @classmethod
    def from_genesis_header(
        cls, base_db: AtomicDatabaseAPI, genesis_header: BlockHeaderAPI
    ) -> "BaseChain":
        chaindb = cls.get_chaindb_class()(base_db)
        chaindb.persist_header(genesis_header)
        return cls(base_db)

    #
    # VM API
    #
    def get_vm(self, at_header: BlockHeaderAPI = None) -> VirtualMachineAPI:
        header = self.ensure_header(at_header)
        vm_class = self.get_vm_class_for_block_number(header.block_number)
        chain_context = ChainContext(self.chain_id)

        return vm_class(
            header=header,
            chaindb=self.chaindb,
            chain_context=chain_context,
            # consensus_context=self.consensus_context,
        )

    #
    # Header API
    #
    def create_header_from_parent(
        self, parent_header: BlockHeaderAPI, **header_params: HeaderParams
    ) -> BlockHeaderAPI:
        return self.get_vm_class_for_block_number(
            block_number=BlockNumber(parent_header.block_number + 1),
        ).create_header_from_parent(parent_header, **header_params)

    def get_block_header_by_hash(self, block_hash: Hash32) -> BlockHeaderAPI:
        validate_word(block_hash, title="Block Hash")
        return self.chaindb.get_block_header_by_hash(block_hash)

    def get_canonical_block_header_by_number(
        self, block_number: BlockNumber
    ) -> BlockHeaderAPI:
        return self.chaindb.get_canonical_block_header_by_number(block_number)

    def get_canonical_head(self) -> BlockHeaderAPI:
        return self.chaindb.get_canonical_head()

    def get_score(self, block_hash: Hash32) -> int:
        return self.headerdb.get_score(block_hash)

    def ensure_header(self, header: BlockHeaderAPI = None) -> BlockHeaderAPI:
        """
        Return ``header`` if it is not ``None``, otherwise return the header
        of the canonical head.
        """
        if header is None:
            head = self.get_canonical_head()
            return self.create_header_from_parent(head)
        else:
            return header

    #
    # Block API
    #
    def get_ancestors(self, limit: int, header: BlockHeaderAPI) -> Tuple[BlockAPI, ...]:
        ancestor_count = min(header.block_number, limit)

        # We construct a temporary block object
        vm_class = self.get_vm_class_for_block_number(header.block_number)
        block_class = vm_class.get_block_class()
        block = block_class(header=header, transactions=[])

        ancestor_generator = iterate(
            compose(
                self.get_block_by_hash,
                operator.attrgetter("parent_hash"),
                operator.attrgetter("header"),
            ),
            block,
        )
        # we peel off the first element from the iterator which will be the
        # temporary block object we constructed.
        next(ancestor_generator)

        return tuple(take(ancestor_count, ancestor_generator))

    def get_block(self) -> BlockAPI:
        return self.get_vm().get_block()

    def get_block_by_hash(self, block_hash: Hash32) -> BlockAPI:
        validate_word(block_hash, title="Block Hash")
        block_header = self.get_block_header_by_hash(block_hash)
        return self.get_block_by_header(block_header)

    def get_block_by_header(self, block_header: BlockHeaderAPI) -> BlockAPI:
        vm = self.get_vm(block_header)
        return vm.get_block()

    def get_canonical_block_by_number(self, block_number: BlockNumber) -> BlockAPI:
        validate_uint256(block_number, title="Block Number")
        return self.get_block_by_hash(
            self.chaindb.get_canonical_block_hash(block_number)
        )

    def get_canonical_block_hash(self, block_number: BlockNumber) -> Hash32:
        return self.chaindb.get_canonical_block_hash(block_number)

    def build_block_with_transactions(self, transactions: Sequence[SignedTransactionAPI],
                                      parent_header: BlockHeaderAPI = None) -> Tuple[BlockAPI, Tuple[ReceiptAPI, ...], Tuple[ComputationAPI, ...]]:
        base_header = self.ensure_header(parent_header)
        vm = self.get_vm(base_header)

        new_header, receipts, computations = vm.apply_all_transactions(
            transactions,
            base_header,
        )

        new_block = vm.set_block_transactions(vm.get_block(), new_header, transactions, receipts)

        return new_block, receipts, computations

    #
    # Transaction API
    #
    def get_canonical_transaction_index(
        self, transaction_hash: Hash32
    ) -> Tuple[BlockNumber, int]:
        return self.chaindb.get_transaction_index(transaction_hash)

    def get_canonical_transaction(
        self, transaction_hash: Hash32
    ) -> SignedTransactionAPI:
        (block_num, index) = self.chaindb.get_transaction_index(transaction_hash)

        transaction = self.get_canonical_transaction_by_index(block_num, index)

        if transaction.hash == transaction_hash:
            return transaction
        else:
            raise TransactionNotFound(
                f"Found transaction {encode_hex(transaction.hash)} "
                f"instead of {encode_hex(transaction_hash)} in block "
                f"{block_num} at {index}"
            )

    def get_canonical_transaction_by_index(
        self, block_number: BlockNumber, index: int
    ) -> SignedTransactionAPI:
        VM_class = self.get_vm_class_for_block_number(block_number)

        return self.chaindb.get_transaction_by_index(
            block_number,
            index,
            VM_class.get_transaction_builder(),
        )

    def create_transaction(self, *args: Any, **kwargs: Any) -> SignedTransactionAPI:
        return self.get_vm().create_transaction(*args, **kwargs)

    def get_transaction_receipt(self, transaction_hash: Hash32) -> ReceiptAPI:
        (
            transaction_block_number,
            transaction_index,
        ) = self.chaindb.get_transaction_index(
            transaction_hash,
        )
        return self.get_transaction_receipt_by_index(
            transaction_block_number, transaction_index
        )

    def get_transaction_receipt_by_index(
        self, block_number: BlockNumber, index: int
    ) -> ReceiptAPI:
        vm = self.get_vm_class_for_block_number(block_number)

        receipt = self.chaindb.get_receipt_by_index(
            block_number,
            index,
            vm.get_receipt_builder(),
        )

        return receipt

    #
    # Execution API
    #
    def get_transaction_result(
        self, transaction: SignedTransactionAPI, at_header: BlockHeaderAPI
    ) -> bytes:
        with self.get_vm(at_header).in_costless_state() as state:
            computation = state.costless_execute_transaction(transaction)

        computation.raise_if_error()
        return computation.output

    def estimate_gas(
        self, transaction: SignedTransactionAPI, at_header: BlockHeaderAPI = None
    ) -> int:
        if at_header is None:
            at_header = self.get_canonical_head()
        with self.get_vm(at_header).in_costless_state() as state:
            return self.gas_estimator(state, transaction)

    def import_block(
        self, block: BlockAPI, perform_validation: bool = True
    ) -> BlockImportResult:
        try:
            parent_header = self.get_block_header_by_hash(block.header.parent_hash)
        except HeaderNotFound:
            raise ValidationError(
                f"Attempt to import block #{block.number}.  "
                f"Cannot import block {block.hash!r} before importing "
                f"its parent block at {block.header.parent_hash!r}"
            )

        base_header_for_import = self.create_header_from_parent(parent_header)
        # Make a copy of the empty header, adding in the expected amount of gas used.
        #   This allows for richer logging in the VM.
        annotated_header = base_header_for_import.copy(gas_used=block.header.gas_used)
        block_result = self.get_vm(annotated_header).import_block(block)
        imported_block = block_result.block

        # Validate the imported block.
        if perform_validation:
            try:
                validate_imported_block_unchanged(imported_block, block)
            except ValidationError:
                self.logger.warning(
                    "Proposed %s doesn't follow EVM rules, rejecting...", block
                )
                raise

        persist_result = self.persist_block(imported_block, perform_validation)
        return BlockImportResult(*persist_result, block_result.meta_witness)

    def persist_block(
        self, block: BlockAPI, perform_validation: bool = True
    ) -> BlockPersistResult:
        if perform_validation:
            self.validate_block(block)

        (
            new_canonical_hashes,
            old_canonical_hashes,
        ) = self.chaindb.persist_block(block)

        self.logger.debug(
            "Persisted block: number %s | hash %s",
            block.number,
            encode_hex(block.hash),
        )

        new_canonical_blocks = tuple(
            self.get_block_by_hash(header_hash) for header_hash in new_canonical_hashes
        )
        old_canonical_blocks = tuple(
            self.get_block_by_hash(header_hash) for header_hash in old_canonical_hashes
        )

        return BlockPersistResult(
            imported_block=block,
            new_canonical_blocks=new_canonical_blocks,
            old_canonical_blocks=old_canonical_blocks,
        )

    #
    # Validation API
    #
    def validate_receipt(self, receipt: ReceiptAPI, at_header: BlockHeaderAPI) -> None:
        VM_class = self.get_vm_class(at_header)
        VM_class.validate_receipt(receipt)

    def validate_block(self, block: BlockAPI) -> None:
        if block.is_genesis:
            raise ValidationError("Cannot validate genesis block this way")
        vm = self.get_vm(block.header)
        parent_header = self.get_block_header_by_hash(block.header.parent_hash)
        vm.validate_header(block.header, parent_header)
        # vm.validate_seal(block.header)
        # vm.validate_seal_extension(block.header, ())
        # self.validate_uncles(block)

    def validate_seal(self, header: BlockHeaderAPI) -> None:
        vm = self.get_vm(header)
        vm.validate_seal(header)




class MiningChain(Chain, MiningChainAPI):
    header: BlockHeaderAPI = None

    def __init__(
        self, base_db: AtomicDatabaseAPI, header: BlockHeaderAPI = None
    ) -> None:
        super().__init__(base_db)
        self.header = self.ensure_header(header)

    def apply_transaction(
        self,
        transaction: SignedTransactionAPI,
    ) -> Tuple[BlockAPI, ReceiptAPI, ComputationAPI]:
        vm = self.get_vm(self.header)
        base_block = vm.get_block()

        receipt, computation = vm.apply_transaction(base_block.header, transaction)
        header_with_receipt = vm.add_receipt_to_header(base_block.header, receipt)

        # since we are building the block locally,
        # we have to persist all the incremental state
        vm.state.persist()
        new_header: BlockHeaderAPI = header_with_receipt.copy(
            state_root=vm.state.state_root
        )

        transactions = base_block.transactions + (transaction,)
        receipts = base_block.get_receipts(self.chaindb) + (receipt,)

        new_block = vm.set_block_transactions(base_block, new_header, transactions, receipts)

        self.header = new_block.header

        return new_block, receipt, computation

    def import_block(
        self, block: BlockAPI, perform_validation: bool = True
    ) -> BlockImportResult:
        result = super().import_block(block, perform_validation)

        self.header = self.ensure_header()
        return result

    def set_header_timestamp(self, timestamp: int) -> None:
        self.header = self.header.copy(timestamp=timestamp)

    @staticmethod
    def _custom_header(base_header: BlockHeaderAPI, **kwargs: Any) -> BlockHeaderAPI:
        # header_fields = {"coinbase"}
        header_fields = {}
        header_params = {k: v for k, v in kwargs.items() if k in header_fields}
        return base_header.copy(**header_params)

    def mine_all(
        self,
        transactions: Sequence[SignedTransactionAPI],
        *args: Any,
        parent_header: BlockHeaderAPI = None,
        **kwargs: Any,
    ) -> Tuple[BlockImportResult, Tuple[ReceiptAPI, ...], Tuple[ComputationAPI, ...]]:
        if parent_header is None:
            base_header = self.header
        else:
            base_header = self.create_header_from_parent(parent_header)

        custom_header = self._custom_header(base_header, **kwargs)
        vm = self.get_vm(custom_header)

        new_header, receipts, computations = vm.apply_all_transactions(
            transactions, base_header
        )

        filled_block = vm.set_block_transactions(vm.get_block(), new_header, transactions, receipts)

        block_result = vm.mine_block(filled_block, *args, **kwargs)
        imported_block = block_result.block

        block_persist_result = self.persist_block(imported_block)
        block_import_result = BlockImportResult(
            *block_persist_result,
        )

        self.header = self.create_header_from_parent(imported_block.header)
        return (block_import_result, receipts, computations)

    def mine_block(self, *args: Any, **kwargs: Any) -> BlockAPI:
        """
        Mine whatever transactions have been incrementally applied so far.
        """
        return self.mine_block_extended(*args, **kwargs).block

    def mine_block_extended(self, *args: Any, **kwargs: Any) -> BlockAndMetaWitness:
        custom_header = self._custom_header(self.header, **kwargs)
        vm = self.get_vm(custom_header)
        current_block = vm.get_block()
        mine_result = vm.mine_block(current_block, *args, **kwargs)
        mined_block = mine_result.block

        self.validate_block(mined_block)

        self.chaindb.persist_block(mined_block)
        self.header = self.create_header_from_parent(mined_block.header)
        return mine_result

    def get_vm(self, at_header: BlockHeaderAPI = None) -> VirtualMachineAPI:
        if at_header is None:
            at_header = self.header

        return super().get_vm(at_header)