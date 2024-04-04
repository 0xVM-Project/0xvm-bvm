import contextlib
import itertools
import logging
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
)

import eth_utils
from cached_property import (
    cached_property,
)
from eth_hash.auto import (
    keccak,
)
from eth_typing import (
    Address,
    Hash32,
)
from eth_utils import (
    ValidationError,
    encode_hex,
)
import rlp

from xvm._utils.datatypes import (
    Configurable,
)
from xvm._utils.db import (
    get_block_header_by_hash,
    get_parent_header,
)
from xvm.abc import (
    AtomicDatabaseAPI,
    BlockAndMetaWitness,
    BlockAPI,
    BlockHeaderAPI,
    ChainContextAPI,
    ChainDatabaseAPI,
    ComputationAPI,
    ExecutionContextAPI,
    ReceiptAPI,
    ReceiptBuilderAPI,
    SignedTransactionAPI,
    StateAPI,
    TransactionBuilderAPI,
    VirtualMachineAPI,
)
from xvm.constants import (
    GENESIS_PARENT_HASH,
    MAX_PREV_HEADER_DEPTH,
    MAX_UNCLES,
)
from xvm.db.trie import (
    make_trie_root_and_nodes,
)
from xvm.exceptions import (
    HeaderNotFound,
)
from xvm.rlp.headers import (
    BlockHeader,
)
from xvm.rlp.sedes import (
    uint32,
)
from xvm.validation import (
    validate_gas_limit,
    validate_length_lte,
)
from xvm.vm.execution_context import (
    ExecutionContext,
)
from xvm.vm.interrupt import (
    EVMMissingData,
)
from xvm.vm.message import (
    Message,
)

if TYPE_CHECKING:
    from xvm.typing import (  # noqa: F401
        Block,
    )


class VM(Configurable, VirtualMachineAPI):
    block_class: Type[BlockAPI] = None
    extra_data_max_bytes: ClassVar[int] = 32
    fork: str = None
    chaindb: ChainDatabaseAPI = None
    _state_class: Type[StateAPI] = None

    _state = None
    _block = None

    cls_logger = logging.getLogger("xvm.vm.base.VM")

    def __init__(
        self,
        header: BlockHeaderAPI,
        chaindb: ChainDatabaseAPI,
        chain_context: ChainContextAPI,
        # consensus_context: ConsensusContextAPI,
    ) -> None:
        self.chaindb = chaindb
        self.chain_context = chain_context
        # self.consensus_context = consensus_context
        self._initial_header = header

    def get_header(self) -> BlockHeaderAPI:
        if self._block is None:
            return self._initial_header
        else:
            return self._block.header

    def get_block(self) -> BlockAPI:
        if self._block is None:
            block_class = self.get_block_class()
            self._block = block_class.from_header(
                header=self._initial_header, chaindb=self.chaindb
            )
        return self._block

    @property
    def state(self) -> StateAPI:
        if self._state is None:
            self._state = self.build_state(
                self.chaindb.db,
                self.get_header(),
                self.chain_context,
                self.previous_hashes,
            )
        return self._state

    @classmethod
    def build_state(
        cls,
        db: AtomicDatabaseAPI,
        header: BlockHeaderAPI,
        chain_context: ChainContextAPI,
        previous_hashes: Iterable[Hash32] = (),
    ) -> StateAPI:
        execution_context = cls.create_execution_context(
            header, previous_hashes, chain_context
        )
        return cls.get_state_class()(db, execution_context, header.state_root)

    # @cached_property
    # def _consensus(self) -> ConsensusAPI:
    #     return self.consensus_class(self.consensus_context)

    #
    # Logging
    #
    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"xvm.vm.base.VM.{self.__class__.__name__}")

    #
    # Execution
    #
    def apply_transaction(
        self, header: BlockHeaderAPI, transaction: SignedTransactionAPI
    ) -> Tuple[ReceiptAPI, ComputationAPI]:
        # self.validate_transaction_against_header(header, transaction)

        # Mark current state as un-revertable, since new transaction is starting...
        self.state.lock_changes()

        computation = self.state.apply_transaction(transaction)
        receipt = self.make_receipt(header, transaction, computation, self.state)
        self.validate_receipt(receipt)

        return receipt, computation

    @classmethod
    def create_execution_context(
        cls,
        header: BlockHeaderAPI,
        prev_hashes: Iterable[Hash32],
        chain_context: ChainContextAPI,
    ) -> ExecutionContextAPI:
        return ExecutionContext(
            # coinbase=fee_recipient,
            timestamp=header.timestamp,
            block_number=header.block_number,
            difficulty=header.difficulty,
            mix_hash=header.mix_hash,
            gas_limit=header.gas_limit,
            prev_hashes=prev_hashes,
            chain_id=chain_context.chain_id,
        )


    def execute_bytecode(
        self,
        origin: Address,
        # gas_price: int,
        gas: int,
        to: Address,
        sender: Address,
        # value: int,
        data: bytes,
        code: bytes,
        code_address: Address = None,
    ) -> ComputationAPI:
        if origin is None:
            origin = sender

        # Construct a message
        message = Message(
            gas=gas,
            to=to,
            sender=sender,
            # value=value,
            data=data,
            code=code,
            code_address=code_address,
        )

        # Construction a tx context
        transaction_context = self.state.get_transaction_context_class()(
            # gas_price=gas_price,
            origin=origin,
        )

        # Execute it in the VM
        return self.state.computation_class.apply_computation(
            self.state,
            message,
            transaction_context,
        )

    def apply_all_transactions(
        self, transactions: Sequence[SignedTransactionAPI], base_header: BlockHeaderAPI
    ) -> Tuple[BlockHeaderAPI, Tuple[SignedTransactionAPI, ...], Tuple[ReceiptAPI, ...], Tuple[ComputationAPI, ...]]:
        vm_header = self.get_header()
        if base_header.block_number != vm_header.block_number:
            raise ValidationError(
                f"This VM instance must only work on block #{self.get_header().block_number}, "  # noqa: E501
                f"but the target header has block #{base_header.block_number}"
            )

        receipts = []
        computations = []
        applied_transactions = []
        previous_header = base_header
        result_header = base_header

        for transaction_index, transaction in enumerate(transactions):
            snapshot = self.state.snapshot()
            try:
                receipt, computation = self.apply_transaction(
                    previous_header,
                    transaction,
                )
            except eth_utils.ValidationError as e:
                # A validation exception usually is raised before VM execution.
                self.logger.debug('Transaction %s raise an validation error, reason: %s', transaction.hash, e)
                continue
            except EVMMissingData:
                self.state.revert(snapshot)
                raise

            result_header = self.add_receipt_to_header(previous_header, receipt)
            previous_header = result_header
            receipts.append(receipt)
            computations.append(computation)
            applied_transactions.append(transaction)

            self.transaction_applied_hook(
                transaction_index,
                transactions,
                vm_header,
                result_header,
                computation,
                receipt,
            )

        receipts_tuple = tuple(receipts)
        computations_tuple = tuple(computations)
        applied_transactions_tuple = tuple(applied_transactions)

        return result_header, applied_transactions_tuple, receipts_tuple, computations_tuple

    #
    # Importing blocks
    #
    def import_block(self, block: BlockAPI) -> BlockAndMetaWitness:
        if self.get_block().number != block.number:
            raise ValidationError(
                f"This VM can only import blocks at number #{self.get_block().number},"
                f" the attempted block was #{block.number}"
            )

        header_params = {
            # "coinbase": block.header.coinbase,
            "difficulty": block.header.difficulty,
            "gas_limit": block.header.gas_limit,
            "timestamp": block.header.timestamp,
            "extra_data": block.header.extra_data,
            "mix_hash": block.header.mix_hash,
            "nonce": block.header.nonce,
            # "uncles_hash": keccak(rlp.encode(block.uncles)),
        }

        block_params = {
            "header": self.configure_header(**header_params),
            # "uncles": block.uncles,
        }

        self._block = self.get_block().copy(**block_params)

        execution_context = self.create_execution_context(
            block.header, self.previous_hashes, self.chain_context
        )

        # Zero out the gas_used before applying transactions. Each applied transaction
        # will increase the gas used in the final new_header.
        header = self.get_header().copy(gas_used=0)

        # we need to re-initialize the `state` to update the execution context.
        self._state = self.get_state_class()(
            self.chaindb.db, execution_context, header.state_root
        )

        # run all of the transactions.
        new_header, receipts, _ = self.apply_all_transactions(
            block.transactions, header
        )

        filled_block = self.set_block_transactions(self.get_block(), new_header, block.transactions, receipts)

        return self.mine_block(filled_block)

    def mine_block(
        self, block: BlockAPI, *args: Any, **kwargs: Any
    ) -> BlockAndMetaWitness:
        packed_block = self.pack_block(block, *args, **kwargs)
        block_result = self.finalize_block(packed_block)

        # Perform validation
        self.validate_block(block_result.block)

        return block_result

    def set_block_transactions(self, base_block: BlockAPI, new_header: BlockHeaderAPI,
                               transactions: Sequence[SignedTransactionAPI], receipts: Sequence[ReceiptAPI]) -> BlockAPI:
        tx_root_hash, tx_kv_nodes = make_trie_root_and_nodes(transactions)
        self.chaindb.persist_trie_data_dict(tx_kv_nodes)

        receipt_root_hash, receipt_kv_nodes = make_trie_root_and_nodes(receipts)
        self.chaindb.persist_trie_data_dict(receipt_kv_nodes)

        block_fields: "Block" = {"transactions": transactions}
        block_header_fields = {
            "transaction_root": tx_root_hash,
            "receipt_root": receipt_root_hash,
        }

        block_fields["header"] = new_header.copy(**block_header_fields)

        return base_block.copy(**block_fields)

    #
    # Finalization
    #
    def _assign_block_rewards(self, block: BlockAPI) -> None:
        # block_reward = self.get_block_reward() + (
        #     len(block.uncles) * self.get_nephew_reward()
        # )

        # EIP-161:
        # Even if block reward is zero, the coinbase is still touched here. This was
        # not likely to ever happen in PoW, except maybe in some very niche cases, but
        # happens now in PoS. In these cases, the coinbase may end up zeroed after the
        # computation and thus should be marked for deletion since it was touched.
        # self.state.delta_balance(block.header.coinbase, block_reward)
        # self.logger.debug(
        #     "BLOCK REWARD: %s -> %s",
        #     block_reward,
        #     encode_hex(block.header.coinbase),
        # )
        #
        # for uncle in block.uncles:
        #     uncle_reward = self.get_uncle_reward(block.number, uncle)
        #     self.logger.debug(
        #         "UNCLE REWARD REWARD: %s -> %s",
        #         uncle_reward,
        #         encode_hex(uncle.coinbase),
        #     )
        #     self.state.delta_balance(uncle.coinbase, uncle_reward)
        return


    def finalize_block(self, block: BlockAPI) -> BlockAndMetaWitness:
        if block.number > 0:
            snapshot = self.state.snapshot()
            try:
                self._assign_block_rewards(block)
            except EVMMissingData:
                self.state.revert(snapshot)
                raise
            else:
                self.state.commit(snapshot)

        # We need to call `persist` here since the state db batches
        # all writes until we tell it to write to the underlying db
        meta_witness = self.state.persist()

        final_block = block.copy(
            header=block.header.copy(state_root=self.state.state_root)
        )

        # self.logger.debug(
        #     "%s reads %d unique node hashes, %d addresses, %d bytecodes, and %d storage slots",  # noqa: E501
        #     final_block,
        #     len(meta_witness.hashes),
        #     len(meta_witness.accounts_queried),
        #     len(meta_witness.account_bytecodes_queried),
        #     meta_witness.total_slots_queried,
        # )

        return BlockAndMetaWitness(final_block, meta_witness)

    def pack_block(self, block: BlockAPI, *args: Any, **kwargs: Any) -> BlockAPI:
        # if "uncles" in kwargs:
        #     uncles = kwargs.pop("uncles")
        #     kwargs.setdefault("uncles_hash", keccak(rlp.encode(uncles)))
        # else:
        #     uncles = block.uncles

        provided_fields = set(kwargs.keys())
        known_fields = set(BlockHeader._meta.field_names)
        unknown_fields = provided_fields.difference(known_fields)

        if unknown_fields:
            raise AttributeError(
                f"Unable to set the field(s) {', '.join(known_fields)} "
                "on the `BlockHeader` class. "
                f"Received the following unexpected fields: {', '.join(unknown_fields)}."  # noqa: E501
            )

        header: BlockHeaderAPI = block.header.copy(**kwargs)

        # packed_block = block.copy(uncles=uncles, header=header)
        packed_block = block.copy(header=header)

        return packed_block