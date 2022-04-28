# future
from __future__ import annotations

# stdlib
from collections.abc import Sequence
import operator
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

# third party
import numpy as np

# relative
from .... import lib
from ....ast.klass import pointerize_args_and_kwargs
from ....core.adp.data_subject_ledger import DataSubjectLedger
from ....core.adp.data_subject_list import DataSubjectList
from ....core.adp.data_subject_list import liststrtonumpyutf8
from ....core.adp.data_subject_list import numpyutf8tolist
from ....core.adp.entity import Entity
from ....core.node.common.action.get_or_set_property_action import (
    GetOrSetPropertyAction,
)
from ....core.node.common.action.get_or_set_property_action import PropertyActions
from ....lib.numpy.array import capnp_deserialize
from ....lib.numpy.array import capnp_serialize
from ....lib.python.util import upcast
from ....util import inherit_tags
from ...common.serde.capnp import CapnpModule
from ...common.serde.capnp import chunk_bytes
from ...common.serde.capnp import combine_bytes
from ...common.serde.capnp import get_capnp_schema
from ...common.serde.capnp import serde_magic_header
from ...common.serde.deserialize import _deserialize as deserialize
from ...common.serde.serializable import serializable
from ...common.serde.serialize import _serialize as serialize
from ...common.uid import UID
from ...node.abstract.node import AbstractNodeClient
from ...node.common.action.run_class_method_action import RunClassMethodAction
from ...pointer.pointer import Pointer
from ..ancestors import AutogradTensorAncestor
from ..broadcastable import is_broadcastable
from ..config import DEFAULT_INT_NUMPY_TYPE
from ..fixed_precision_tensor import FixedPrecisionTensor
from ..lazy_repeat_array import lazyrepeatarray
from ..passthrough import AcceptableSimpleType  # type: ignore
from ..passthrough import PassthroughTensor  # type: ignore
from ..passthrough import SupportedChainType  # type: ignore
from ..passthrough import is_acceptable_simple_type  # type: ignore
from ..smpc import utils
from ..smpc.mpc_tensor import MPCTensor
from ..smpc.mpc_tensor import ShareTensor
from ..smpc.utils import TYPE_TO_RING_SIZE
from .adp_tensor import ADPTensor
from .gamma_tensor import GammaTensor
from .initial_gamma import InitialGammaTensor
from .initial_gamma import IntermediateGammaTensor


@serializable(recursive_serde=True)
class TensorWrappedNDimEntityPhiTensorPointer(Pointer):
    __name__ = "TensorWrappedNDimEntityPhiTensorPointer"
    __module__ = "syft.core.tensor.autodp.ndim_entity_phi"
    __attr_allowlist__ = [
        # default pointer attrs
        "client",
        "id_at_location",
        "object_type",
        "tags",
        "description",
        # ndim attrs
        "entities",
        "min_vals",
        "max_vals",
        "public_dtype",
        "public_shape",
    ]

    __serde_overrides__ = {
        "client": [lambda x: x.address, lambda y: y],
        "public_shape": [lambda x: x, lambda y: upcast(y)],
    }
    _exhausted = False
    is_enum = False

    def __init__(
        self,
        entities: DataSubjectList,
        min_vals: np.typing.ArrayLike,
        max_vals: np.typing.ArrayLike,
        client: Any,
        id_at_location: Optional[UID] = None,
        object_type: str = "",
        tags: Optional[List[str]] = None,
        description: str = "",
        public_shape: Optional[Tuple[int, ...]] = None,
        public_dtype: Optional[np.dtype] = None,
    ):
        super().__init__(
            client=client,
            id_at_location=id_at_location,
            object_type=object_type,
            tags=tags,
            description=description,
        )

        self.min_vals = min_vals
        self.max_vals = max_vals
        self.entities = entities
        self.public_shape = public_shape
        self.public_dtype = public_dtype

    # TODO: Modify for large arrays
    @property
    def synthetic(self) -> np.ndarray:
        return (
            np.random.rand(*list(self.public_shape))  # type: ignore
            * (self.max_vals.to_numpy() - self.min_vals.to_numpy())
            + self.min_vals.to_numpy()
        ).astype(self.public_dtype)

    def __repr__(self) -> str:
        return (
            self.synthetic.__repr__()
            + "\n\n (The data printed above is synthetic - it's an imitation of the real data.)"
        )

    def share(self, *parties: Tuple[AbstractNodeClient, ...]) -> MPCTensor:
        all_parties = list(parties) + [self.client]
        ring_size = TYPE_TO_RING_SIZE.get(self.public_dtype, None)
        self_mpc = MPCTensor(
            secret=self,
            shape=self.public_shape,
            ring_size=ring_size,
            parties=all_parties,
        )
        return self_mpc

    def _apply_tensor_op(self, other: Any, op_str: str) -> Any:
        # we want to get the return type which matches the attr_path_and_name
        # so we ask lib_ast for the return type name that matches out
        # attr_path_and_name and then use that to get the actual pointer klass
        # then set the result to that pointer klass

        # We always maintain a Tensor hierarchy Tensor ---> NDEPT--> Actual Data
        attr_path_and_name = f"syft.core.tensor.tensor.Tensor.__{op_str}__"

        result = TensorWrappedNDimEntityPhiTensorPointer(
            entities=self.entities,
            min_vals=self.min_vals,
            max_vals=self.max_vals,
            client=self.client,
        )

        # QUESTION can the id_at_location be None?
        result_id_at_location = getattr(result, "id_at_location", None)

        if result_id_at_location is not None:
            # first downcast anything primitive which is not already PyPrimitive
            (
                downcast_args,
                downcast_kwargs,
            ) = lib.python.util.downcast_args_and_kwargs(args=[other], kwargs={})

            # then we convert anything which isnt a pointer into a pointer
            pointer_args, pointer_kwargs = pointerize_args_and_kwargs(
                args=downcast_args,
                kwargs=downcast_kwargs,
                client=self.client,
                gc_enabled=False,
            )

            cmd = RunClassMethodAction(
                path=attr_path_and_name,
                _self=self,
                args=pointer_args,
                kwargs=pointer_kwargs,
                id_at_location=result_id_at_location,
                address=self.client.address,
            )
            self.client.send_immediate_msg_without_reply(msg=cmd)

        inherit_tags(
            attr_path_and_name=attr_path_and_name,
            result=result,
            self_obj=self,
            args=[other],
            kwargs={},
        )

        result_public_shape = None

        if isinstance(other, TensorWrappedNDimEntityPhiTensorPointer):
            other_shape = other.public_shape
            other_dtype = other.public_dtype
        elif isinstance(other, (int, float)):
            other_shape = (1,)
            other_dtype = DEFAULT_INT_NUMPY_TYPE
        elif isinstance(other, bool):
            other_shape = (1,)
            other_dtype = np.dtype("bool")
        elif isinstance(other, np.ndarray):
            other_shape = other.shape
            other_dtype = other.dtype
        else:
            raise ValueError(
                f"Invalid Type for TensorWrappedNDimEntityPhiTensorPointer:{type(other)}"
            )

        if self.public_shape is not None and other_shape is not None:
            result_public_shape = utils.get_shape(
                op_str, self.public_shape, other_shape
            )

        if self.public_dtype is None or other_dtype is None:
            if self.public_dtype != other_dtype:
                raise ValueError(
                    f"Dtype for self: {self.public_dtype} and other :{other_dtype} should not be None"
                )
        result_public_dtype = self.public_dtype

        result.public_shape = result_public_shape
        result.public_dtype = result_public_dtype

        return result

    @staticmethod
    def _apply_op(
        self: TensorWrappedNDimEntityPhiTensorPointer,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
        op_str: str,
    ) -> Union[MPCTensor, TensorWrappedNDimEntityPhiTensorPointer]:
        """Performs the operation based on op_str

        Args:
            other (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]): second operand.

        Returns:
            Tuple[MPCTensor,Union[MPCTensor,int,float,np.ndarray]] : Result of the operation
        """
        op = getattr(operator, op_str)

        if (
            isinstance(other, TensorWrappedNDimEntityPhiTensorPointer)
            and self.client != other.client
        ):

            parties = [self.client, other.client]

            self_mpc = MPCTensor(secret=self, shape=self.public_shape, parties=parties)
            other_mpc = MPCTensor(
                secret=other, shape=other.public_shape, parties=parties
            )

            return op(self_mpc, other_mpc)

        elif isinstance(other, MPCTensor):

            return op(other, self)

        return self._apply_tensor_op(other=other, op_str=op_str)

    def __add__(
        self,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
    ) -> Union[TensorWrappedNDimEntityPhiTensorPointer, MPCTensor]:
        """Apply the "add" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.

        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        return TensorWrappedNDimEntityPhiTensorPointer._apply_op(self, other, "add")

    def __sub__(
        self,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
    ) -> Union[TensorWrappedNDimEntityPhiTensorPointer, MPCTensor]:
        """Apply the "sub" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.

        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        return TensorWrappedNDimEntityPhiTensorPointer._apply_op(self, other, "sub")

    def __mul__(
        self,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
    ) -> Union[TensorWrappedNDimEntityPhiTensorPointer, MPCTensor]:
        """Apply the "mul" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.

        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        return TensorWrappedNDimEntityPhiTensorPointer._apply_op(self, other, "mul")

    def __matmul__(
        self,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
    ) -> Union[TensorWrappedNDimEntityPhiTensorPointer, MPCTensor]:
        """Apply the "matmul" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.

        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        return TensorWrappedNDimEntityPhiTensorPointer._apply_op(self, other, "matmul")

    def __lt__(
        self,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
    ) -> Union[TensorWrappedNDimEntityPhiTensorPointer, MPCTensor]:
        """Apply the "lt" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.

        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        return TensorWrappedNDimEntityPhiTensorPointer._apply_op(self, other, "lt")

    def __gt__(
        self,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
    ) -> Union[TensorWrappedNDimEntityPhiTensorPointer, MPCTensor]:
        """Apply the "gt" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.

        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        return TensorWrappedNDimEntityPhiTensorPointer._apply_op(self, other, "gt")

    def __ge__(
        self,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
    ) -> Union[TensorWrappedNDimEntityPhiTensorPointer, MPCTensor]:
        """Apply the "ge" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.

        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        return TensorWrappedNDimEntityPhiTensorPointer._apply_op(self, other, "ge")

    def __le__(
        self,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
    ) -> Union[TensorWrappedNDimEntityPhiTensorPointer, MPCTensor]:
        """Apply the "le" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.

        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        return TensorWrappedNDimEntityPhiTensorPointer._apply_op(self, other, "le")

    def __eq__(  # type: ignore
        self,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
    ) -> Union[TensorWrappedNDimEntityPhiTensorPointer, MPCTensor]:
        """Apply the "eq" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.

        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        return TensorWrappedNDimEntityPhiTensorPointer._apply_op(self, other, "eq")

    def __ne__(  # type: ignore
        self,
        other: Union[
            TensorWrappedNDimEntityPhiTensorPointer, MPCTensor, int, float, np.ndarray
        ],
    ) -> Union[TensorWrappedNDimEntityPhiTensorPointer, MPCTensor]:
        """Apply the "ne" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.

        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        return TensorWrappedNDimEntityPhiTensorPointer._apply_op(self, other, "ne")

    def concatenate(
        self,
        other: TensorWrappedNDimEntityPhiTensorPointer,
        *args: List[Any],
        **kwargs: Dict[str, Any],
    ) -> MPCTensor:
        """Apply the "add" operation between "self" and "other"

        Args:
            y (Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor,int,float,np.ndarray]) : second operand.


        Returns:
            Union[TensorWrappedNDimEntityPhiTensorPointer,MPCTensor] : Result of the operation.
        """
        if not isinstance(other, TensorWrappedNDimEntityPhiTensorPointer):
            raise ValueError(
                f"Concatenate works only for TensorWrappedNDimEntityPhiTensorPointer got type: {type(other)}"
            )

        if self.client != other.client:

            parties = [self.client, other.client]

            self_mpc = MPCTensor(secret=self, shape=self.public_shape, parties=parties)
            other_mpc = MPCTensor(
                secret=other, shape=other.public_shape, parties=parties
            )

            return self_mpc.concatenate(other_mpc, *args, **kwargs)

        else:
            raise ValueError(
                "Concatenate method currently works only between two different clients."
            )

    @property
    def T(self) -> TensorWrappedNDimEntityPhiTensorPointer:
        # We always maintain a Tensor hierarchy Tensor ---> NDEPT--> Actual Data
        attr_path_and_name = "syft.core.tensor.tensor.Tensor.T"

        result = TensorWrappedNDimEntityPhiTensorPointer(
            entities=self.entities,
            min_vals=self.min_vals,
            max_vals=self.max_vals,
            client=self.client,
        )

        # QUESTION can the id_at_location be None?
        result_id_at_location = getattr(result, "id_at_location", None)

        if result_id_at_location is not None:
            # first downcast anything primitive which is not already PyPrimitive
            (
                downcast_args,
                downcast_kwargs,
            ) = lib.python.util.downcast_args_and_kwargs(args=[], kwargs={})

            # then we convert anything which isnt a pointer into a pointer
            pointer_args, pointer_kwargs = pointerize_args_and_kwargs(
                args=downcast_args,
                kwargs=downcast_kwargs,
                client=self.client,
                gc_enabled=False,
            )

            cmd = GetOrSetPropertyAction(
                path=attr_path_and_name,
                id_at_location=result_id_at_location,
                address=self.client.address,
                _self=self,
                args=pointer_args,
                kwargs=pointer_kwargs,
                action=PropertyActions.GET,
                map_to_dyn=False,
            )
            self.client.send_immediate_msg_without_reply(msg=cmd)

        inherit_tags(
            attr_path_and_name=attr_path_and_name,
            result=result,
            self_obj=self,
            args=[],
            kwargs={},
        )

        return result

    def to_local_object_without_private_data_child(self) -> NDimEntityPhiTensor:
        """Convert this pointer into a partial version of the NDimEntityPhiTensor but without
        any of the private data therein."""
        # relative
        from ..tensor import Tensor

        public_shape = getattr(self, "public_shape", None)
        public_dtype = getattr(self, "public_dtype", None)
        return Tensor(
            child=NDimEntityPhiTensor(
                child=FixedPrecisionTensor(value=None),
                entities=self.entities,
                min_vals=self.min_vals,  # type: ignore
                max_vals=self.max_vals,  # type: ignore
            ),
            public_shape=public_shape,
            public_dtype=public_dtype,
        )


@serializable(capnp_bytes=True)
class NDimEntityPhiTensor(PassthroughTensor, AutogradTensorAncestor, ADPTensor):
    PointerClassOverride = TensorWrappedNDimEntityPhiTensorPointer
    # __attr_allowlist__ = ["child", "min_vals", "max_vals", "entities"]
    __slots__ = (
        "child",
        "min_vals",
        "max_vals",
        "entities",
    )

    def __init__(
        self,
        child: Sequence,
        entities: Union[List[Entity], DataSubjectList],
        min_vals: np.ndarray,
        max_vals: np.ndarray,
    ) -> None:
        if isinstance(child, FixedPrecisionTensor):
            # child = the actual private data
            super().__init__(child)
        else:
            super().__init__(FixedPrecisionTensor(value=child))

        # lazyrepeatarray matching the shape of child
        if not isinstance(min_vals, lazyrepeatarray):
            min_vals = lazyrepeatarray(data=min_vals, shape=child.shape)  # type: ignore
        if not isinstance(max_vals, lazyrepeatarray):
            max_vals = lazyrepeatarray(data=max_vals, shape=child.shape)  # type: ignore
        self.min_vals = min_vals
        self.max_vals = max_vals

        if not isinstance(entities, DataSubjectList):
            entities = DataSubjectList.from_objs(entities)

        self.entities = entities

    @property
    def proxy_public_kwargs(self) -> Dict[str, Any]:
        return {
            "min_vals": self.min_vals,
            "max_vals": self.max_vals,
            "entities": self.entities,
        }

    @staticmethod
    def from_rows(rows: Sequence) -> NDimEntityPhiTensor:
        # relative
        from .single_entity_phi import SingleEntityPhiTensor

        if len(rows) < 1 or not isinstance(rows[0], SingleEntityPhiTensor):
            raise Exception(
                "NDimEntityPhiTensor.from_rows requires a list of SingleEntityPhiTensors"
            )

        # create lazyrepeatarrays of the first element
        first_row = rows[0]
        min_vals = lazyrepeatarray(
            data=first_row.min_vals,
            shape=tuple([len(rows)] + list(first_row.min_vals.shape)),
        )
        max_vals = lazyrepeatarray(
            data=first_row.max_vals,
            shape=tuple([len(rows)] + list(first_row.max_vals.shape)),
        )

        # collect entities and children into numpy arrays
        entity_list = []
        child_list = []
        for row in rows:
            entity_list.append(row.entity)
            child_list.append(row.child)
        entities = DataSubjectList.from_objs(entities=entity_list)
        child = np.stack(child_list)

        # use new constructor
        return NDimEntityPhiTensor(
            child=child,
            min_vals=min_vals,
            max_vals=max_vals,
            entities=entities,
        )

    # def init_pointer(
    #     self,
    #     client: Any,
    #     id_at_location: Optional[UID] = None,
    #     object_type: str = "",
    #     tags: Optional[List[str]] = None,
    #     description: str = "",
    # ) -> TensorWrappedNDimEntityPhiTensorPointer:
    #     return TensorWrappedNDimEntityPhiTensorPointer(
    #         # Arguments specifically for SEPhiTensor
    #         entities=self.entities,
    #         min_vals=self.min_vals,
    #         max_vals=self.max_vals,
    #         # Arguments required for a Pointer to work
    #         client=client,
    #         id_at_location=id_at_location,
    #         object_type=object_type,
    #         tags=tags,
    #         description=description,
    #     )

    @property
    def gamma(self) -> GammaTensor:
        """Property to cast this tensor into a GammaTensor"""
        return self.create_gamma()

    def copy(self, order: Optional[str] = "K") -> NDimEntityPhiTensor:
        """Return copy of the given object"""

        return NDimEntityPhiTensor(
            child=self.child.copy(order=order),
            min_vals=self.min_vals.copy(order=order),
            max_vals=self.max_vals.copy(order=order),
            entities=self.entities.copy(order=order),
        )

    def all(self) -> bool:
        return self.child.all()

    def any(self) -> bool:
        return self.child.any()

    def copy_with(self, child: np.ndarray) -> NDimEntityPhiTensor:
        new_tensor = self.copy()
        new_tensor.child = child
        return new_tensor

    def create_gamma(self) -> GammaTensor:
        """Return a new Gamma tensor based on this phi tensor"""
        # TODO: check if values needs to be a JAX array or if numpy will suffice
        fpt_values = self.child
        value = (
            self.child.child.child
            if isinstance(self.child.child, ShareTensor)
            else self.child.child
        )
        gamma_tensor = GammaTensor(
            value=value,
            data_subjects=self.entities,
            min_val=self.min_vals.to_numpy(),
            max_val=self.max_vals.to_numpy(),
            fpt_values=fpt_values,
        )
        return gamma_tensor

    def publish(
        self,
        get_budget_for_user: Callable,
        deduct_epsilon_for_user: Callable,
        ledger: DataSubjectLedger,
        sigma: float,
    ) -> AcceptableSimpleType:
        print("PUBLISHING TO GAMMA:")
        print(self.child)

        gamma = self.gamma
        # gamma.func = lambda x: x
        gamma.state[gamma.id] = gamma

        res = gamma.publish(
            get_budget_for_user=get_budget_for_user,
            deduct_epsilon_for_user=deduct_epsilon_for_user,
            ledger=ledger,
            sigma=sigma,
        )
        fpt_values = gamma.fpt_values

        if fpt_values is None:
            raise ValueError(
                "FixedPrecisionTensor values should not be None after publish"
            )

        if isinstance(fpt_values.child, ShareTensor):
            fpt_values.child.child = res
        else:
            fpt_values.child = res

        print("Final FPT Values", fpt_values)

        return fpt_values

    @property
    def value(self) -> np.ndarray:
        return self.child

    def astype(self, np_type: np.dtype) -> NDimEntityPhiTensor:
        return self.__class__(
            child=self.child.astype(np_type),
            entities=self.entities,
            min_vals=self.min_vals.astype(np_type),
            max_vals=self.max_vals.astype(np_type),
            # scalar_manager=self.scalar_manager,
        )

    @property
    def shape(self) -> Tuple[Any, ...]:
        return self.child.shape

    def __repr__(self) -> str:
        """Pretty print some information, optimized for Jupyter notebook viewing."""
        return (
            f"{self.__class__.__name__}(child={self.child}, "
            + f"min_vals={self.min_vals}, max_vals={self.max_vals})"
        )

    def __eq__(  # type: ignore
        self, other: Any
    ) -> Union[NDimEntityPhiTensor, IntermediateGammaTensor, GammaTensor]:
        # TODO: what about entities and min / max values?
        if is_acceptable_simple_type(other) or len(self.child) == len(other.child):
            gamma_output = False
            if is_acceptable_simple_type(other):
                result = self.child == other
            else:
                # check entities match, if they dont gamma_output = True
                #
                result = self.child == other.child
                if isinstance(result, InitialGammaTensor):
                    gamma_output = True
            if not gamma_output:
                # min_vals=self.min_vals * 0.0,
                # max_vals=self.max_vals * 0.0 + 1.0,
                return self.copy_with(child=result)
            else:
                return self.copy_with(child=result).gamma
        else:
            raise Exception(
                "Tensor dims do not match for __eq__: "
                + f"{len(self.child)} != {len(other.child)}"
            )

    def __add__(
        self, other: SupportedChainType
    ) -> Union[NDimEntityPhiTensor, GammaTensor]:

        # if the tensor being added is also private
        if isinstance(other, NDimEntityPhiTensor):
            if self.entities != other.entities:
                return self.gamma + other.gamma

            return NDimEntityPhiTensor(
                child=self.child + other.child,
                min_vals=self.min_vals + other.min_vals,
                max_vals=self.max_vals + other.max_vals,
                entities=self.entities,
                # scalar_manager=self.scalar_manager,
            )

        # if the tensor being added is a public tensor / int / float / etc.
        elif is_acceptable_simple_type(other):
            return NDimEntityPhiTensor(
                child=self.child + other,
                min_vals=self.min_vals + other,
                max_vals=self.max_vals + other,
                entities=self.entities,
                # scalar_manager=self.scalar_manager,
            )

        elif isinstance(other, GammaTensor):
            return self.gamma + other
        else:
            print("Type is unsupported:" + str(type(other)))
            raise NotImplementedError

    def __sub__(
        self, other: SupportedChainType
    ) -> Union[NDimEntityPhiTensor, GammaTensor]:

        if isinstance(other, NDimEntityPhiTensor):
            if self.entities != other.entities:
                # return self.gamma - other.gamma
                raise NotImplementedError

            data = self.child - other.child

            min_min = self.min_vals.data - other.min_vals.data
            min_max = self.min_vals.data - other.max_vals.data
            max_min = self.max_vals.data - other.min_vals.data
            max_max = self.max_vals.data - other.max_vals.data
            _min_vals = np.minimum.reduce([min_min, min_max, max_min, max_max])
            _max_vals = np.maximum.reduce([min_min, min_max, max_min, max_max])
            min_vals = self.min_vals.copy()
            min_vals.data = _min_vals
            max_vals = self.max_vals.copy()
            max_vals.data = _max_vals

            entities = self.entities

        elif is_acceptable_simple_type(other):
            if isinstance(other, np.ndarray):
                if not is_broadcastable(other.shape, self.child.shape):  # type: ignore
                    raise Exception(
                        f"Shapes do not match for subtraction: {self.child.shape} and {other.shape}"
                    )
            data = self.child - other
            min_vals = self.min_vals - other
            max_vals = self.max_vals - other
            entities = self.entities
        else:
            raise NotImplementedError
        return NDimEntityPhiTensor(
            child=data,
            entities=entities,
            min_vals=min_vals,
            max_vals=max_vals,
        )

    def __mul__(
        self, other: SupportedChainType
    ) -> Union[NDimEntityPhiTensor, GammaTensor]:

        if isinstance(other, NDimEntityPhiTensor):
            if self.entities != other.entities:
                print("Entities are not the same?!?!?!")
                return self.gamma * other.gamma

            data = self.child * other.child

            min_min = self.min_vals.data * other.min_vals.data
            min_max = self.min_vals.data * other.max_vals.data
            max_min = self.max_vals.data * other.min_vals.data
            max_max = self.max_vals.data * other.max_vals.data

            _min_vals = np.min([min_min, min_max, max_min, max_max], axis=0)  # type: ignore
            _max_vals = np.max([min_min, min_max, max_min, max_max], axis=0)  # type: ignore
            min_vals = self.min_vals.copy()
            min_vals.data = _min_vals
            max_vals = self.max_vals.copy()
            max_vals.data = _max_vals

            entities = self.entities

            return NDimEntityPhiTensor(
                child=data,
                entities=entities,
                min_vals=min_vals,
                max_vals=max_vals,
            )
        elif is_acceptable_simple_type(other):

            data = self.child * other

            min_min = self.min_vals.data * other
            min_max = self.min_vals.data * other
            max_min = self.max_vals.data * other
            max_max = self.max_vals.data * other

            _min_vals = np.min([min_min, min_max, max_min, max_max], axis=0)  # type: ignore
            _max_vals = np.max([min_min, min_max, max_min, max_max], axis=0)  # type: ignore
            min_vals = self.min_vals.copy()
            min_vals.data = _min_vals
            max_vals = self.max_vals.copy()
            max_vals.data = _max_vals

            entities = self.entities

            return NDimEntityPhiTensor(
                child=data,
                entities=entities,
                min_vals=min_vals,
                max_vals=max_vals,
            )
        else:
            return NotImplementedError  # type: ignore

    def __matmul__(
        self, other: Union[np.ndarray, NDimEntityPhiTensor]
    ) -> Union[NDimEntityPhiTensor, GammaTensor]:
        if not isinstance(other, (np.ndarray, NDimEntityPhiTensor)):
            raise Exception(
                f"Matrix multiplication not yet implemented for type {type(other)}"
            )
        else:
            # Modify before merge, to know is broadcast is actually necessary
            if False:  # and not is_broadcastable(self.shape, other.shape):
                raise Exception(
                    f"Shapes not broadcastable: {self.shape} and {other.shape}"
                )
            else:
                if isinstance(other, np.ndarray):
                    data = self.child.__matmul__(other)
                    min_vals = self.min_vals.__matmul__(other)
                    max_vals = self.max_vals.__matmul__(other)
                elif isinstance(other, NDimEntityPhiTensor):
                    if self.entities != other.entities:
                        # return convert_to_gamma_tensor(self).__matmul__(convert_to_gamma_tensor(other))
                        raise NotImplementedError
                    else:
                        data = self.child.__matmul__(other.child)
                        # _min_vals = np.array(
                        #     [self.min_vals.data.__matmul__(other.min_vals.data)]
                        # )
                        # _max_vals = np.array(
                        #     [self.max_vals.data.__matmul__(other.max_vals.data)]
                        # )
                        # min_vals = self.min_vals.copy()
                        # min_vals.data = _min_vals
                        # max_vals = self.max_vals.copy()
                        # max_vals.data = _max_vals
                        min_vals = self.min_vals.__matmul__(other.min_vals)
                        max_vals = self.max_vals.__matmul__(other.max_vals)

                else:
                    raise NotImplementedError

                return NDimEntityPhiTensor(
                    child=data,
                    max_vals=max_vals,
                    min_vals=min_vals,
                    entities=self.entities,
                )

    def transpose(self, *args: Any, **kwargs: Any) -> NDimEntityPhiTensor:
        """Transposes self.child, min_vals, and max_vals if these can be transposed, otherwise doesn't change them."""
        data: Sequence
        if (
            isinstance(self.child, int)
            or isinstance(self.child, float)
            or isinstance(self.child, bool)
        ):
            # For these data types, the transpose operation is meaningless, so don't change them.
            data = self.child  # type: ignore
            print(
                f"Warning: Tensor data was of type {type(data)}, transpose operation had no effect."
            )
        else:
            data = self.child.transpose(*args)

        # TODO: Should we give warnings for min_val and max_val being single floats/integers/booleans too?
        if (
            isinstance(self.min_vals, int)
            or isinstance(self.min_vals, float)
            or isinstance(self.min_vals, bool)
        ):
            # For these data types, the transpose operation is meaningless, so don't change them.
            min_vals = self.min_vals
            # print(f'Warning: Tensor data was of type {type(data)}, transpose operation had no effect.')
        else:
            min_vals = self.min_vals.transpose(*args)

        if (
            isinstance(self.max_vals, int)
            or isinstance(self.max_vals, float)
            or isinstance(self.max_vals, bool)
        ):
            # For these data types, the transpose operation is meaningless, so don't change them.
            max_vals = self.max_vals
            # print(f'Warning: Tensor data was of type {type(data)}, transpose operation had no effect.')
        else:
            max_vals = self.max_vals.transpose(*args)

        return NDimEntityPhiTensor(
            child=data,
            entities=self.entities,
            min_vals=min_vals,
            max_vals=max_vals,
        )

    def concatenate(
        self,
        other: Union[np.ndarray, NDimEntityPhiTensor],
        *args: List[Any],
        **kwargs: Dict[str, Any],
    ) -> Union[NDimEntityPhiTensor, GammaTensor]:

        # if the tensor being added is also private
        if isinstance(other, NDimEntityPhiTensor):
            if self.entities != other.entities:
                return self.gamma + other.gamma

            return NDimEntityPhiTensor(
                child=self.child.concatenate(other.child, *args, **kwargs),
                min_vals=self.min_vals.concatenate(other.min_vals, *args, **kwargs),
                max_vals=self.max_vals.concatenate(other.max_vals, *args, **kwargs),
                entities=self.entities,
            )

        elif is_acceptable_simple_type(other):
            raise NotImplementedError
        else:
            print("Type is unsupported:" + str(type(other)))
            raise NotImplementedError

    def __lt__(
        self, other: SupportedChainType
    ) -> Union[NDimEntityPhiTensor, GammaTensor]:

        # if the tensor being compared is also private
        if isinstance(other, NDimEntityPhiTensor):

            if self.entities != other.entities:
                # return self.gamma < other.gamma
                raise NotImplementedError

            if len(self.child) != len(other.child):
                raise Exception(
                    f"Tensor dims do not match for __lt__: {len(self.child)} != {len(other.child)}"  # type: ignore
                )

            data = (
                self.child < other.child
            )  # the * 1 just makes sure it returns integers instead of True/False
            min_vals = self.min_vals * 0
            max_vals = (self.max_vals * 0) + 1
            entities = self.entities

            return NDimEntityPhiTensor(
                child=data,
                entities=entities,
                min_vals=min_vals,
                max_vals=max_vals,
            )

        # if the tensor being compared is a public tensor / int / float / etc.
        elif is_acceptable_simple_type(other):

            data = self.child < other
            min_vals = self.min_vals * 0
            max_vals = (self.max_vals * 0) + 1
            entities = self.entities

            return NDimEntityPhiTensor(
                child=data,
                entities=entities,
                min_vals=min_vals,
                max_vals=max_vals,
            )

        else:
            return NotImplementedError  # type: ignore

    def __gt__(
        self, other: SupportedChainType
    ) -> Union[NDimEntityPhiTensor, GammaTensor]:

        # if the tensor being compared is also private
        if isinstance(other, NDimEntityPhiTensor):

            if self.entities != other.entities:
                # return self.gamma < other.gamma
                raise NotImplementedError

            if len(self.child) != len(other.child):
                raise Exception(
                    f"Tensor dims do not match for __gt__: {len(self.child)} != {len(other.child)}"  # type: ignore
                )

            data = (
                self.child > other.child
            )  # the * 1 just makes sure it returns integers instead of True/False
            min_vals = self.min_vals * 0
            max_vals = (self.max_vals * 0) + 1
            entities = self.entities

            return NDimEntityPhiTensor(
                child=data,
                entities=entities,
                min_vals=min_vals,
                max_vals=max_vals,
            )

        # if the tensor being compared is a public tensor / int / float / etc.
        elif is_acceptable_simple_type(other):

            data = self.child > other
            min_vals = self.min_vals * 0
            max_vals = (self.max_vals * 0) + 1
            entities = self.entities

            return NDimEntityPhiTensor(
                child=data,
                entities=entities,
                min_vals=min_vals,
                max_vals=max_vals,
            )
        else:
            raise NotImplementedError  # type: ignore

    # Re enable after testing
    # def dot(
    #     self, other: Union[NDimEntityPhiTensor, GammaTensor, np.ndarray]
    # ) -> Union[NDimEntityPhiTensor, GammaTensor]:
    #     if isinstance(other, np.ndarray):
    #         print("We here or what?")
    #         return NDimEntityPhiTensor(
    #             child=np.dot(self.child, other),
    #             min_vals=np.dot(self.min_vals, other),
    #             max_vals=np.dot(self.max_vals, other),
    #             entities=self.entities,
    #         )
    #     elif isinstance(other, NDimEntityPhiTensor):
    #         if (
    #             len(self.entities.one_hot_lookup) > 1
    #             or len(other.entities.one_hot_lookup) > 1
    #         ):
    #             return self.gamma.dot(other.gamma)
    #         elif (
    #             len(self.entities.one_hot_lookup) == 1
    #             and len(other.entities.one_hot_lookup) == 1
    #             and self.entities.one_hot_lookup != other.entities.one_hot_lookup
    #         ):
    #             return self.gamma.dot(other.gamma)
    #     elif isinstance(other, GammaTensor):
    #         return self.gamma.dot(other)
    #     else:
    #         raise NotImplementedError

    def sum(
        self, axis: Optional[Union[int, Tuple[int, ...]]] = None
    ) -> Union[NDimEntityPhiTensor, GammaTensor]:
        # TODO: Add support for axes arguments later
        if len(self.entities.one_hot_lookup) == 1:
            return NDimEntityPhiTensor(
                child=self.child.sum(),
                min_vals=self.min_vals.sum(axis=None),
                max_vals=self.max_vals.sum(axis=None),
                entities=DataSubjectList.from_objs(
                    self.entities.one_hot_lookup[0]
                ),  # Need to check this
            )

        # TODO: Expand this later to include more args/kwargs
        return GammaTensor(
            value=np.array(self.child.child.sum()),
            data_subjects=self.entities.sum(),
            min_val=float(self.min_vals.sum(axis=None)),
            max_val=float(self.max_vals.sum(axis=None)),
        )

    def __ne__(  # type: ignore
        self, other: Any
    ) -> Union[NDimEntityPhiTensor, IntermediateGammaTensor, GammaTensor]:
        # TODO: what about entities and min / max values?
        if is_acceptable_simple_type(other) or len(self.child) == len(other.child):
            gamma_output = False
            if is_acceptable_simple_type(other):
                result = self.child != other
            else:
                # check entities match, if they dont gamma_output = True
                #
                result = self.child != other.child
                if isinstance(result, InitialGammaTensor):
                    gamma_output = True
            if not gamma_output:
                return self.copy_with(child=result)
            else:
                return self.copy_with(child=result).gamma
        else:
            raise Exception(
                "Tensor dims do not match for __eq__: "
                + f"{len(self.child)} != {len(other.child)}"
            )

    def __neg__(self) -> NDimEntityPhiTensor:

        return NDimEntityPhiTensor(
            child=self.child * -1,
            min_vals=self.max_vals * -1,
            max_vals=self.min_vals * -1,
            entities=self.entities,
        )

    def __pos__(self) -> NDimEntityPhiTensor:
        return NDimEntityPhiTensor(
            child=self.child,
            min_vals=self.min_vals,
            max_vals=self.max_vals,
            entities=self.entities,
        )

    def dot(
        self, other: Union[AcceptableSimpleType, NDimEntityPhiTensor, GammaTensor]
    ) -> Union[NDimEntityPhiTensor, GammaTensor]:  # type: ignore
        if is_acceptable_simple_type(other):
            # Return NDEPT
            if isinstance(other, np.ndarray):
                return NDimEntityPhiTensor(
                    child=self.child.dot(other),
                    min_vals=self.child.dot(other),
                    max_vals=self.child.dot(other),
                    entities=self.entities
                )
            else:
                # TODO: Should we should cast it to an array of the same size for them?
                raise Exception(f"We can't take a dot product with object of type: {type(other)}. "
                                f"Please try casting this to an array instead")
        elif isinstance(other, NDimEntityPhiTensor):
            # TODO: Improve equality for DataSubjectLists
            if len(self.entities.one_hot_lookup) > 1 or len(other.entities.one_hot_lookup) > 1:
                # Return GammaTensor
                raise NotImplementedError
            elif self.entities.one_hot_lookup == other.entities.one_hot_lookup:
                return NDimEntityPhiTensor(
                    child=self.child.dot(other.child),
                    min_vals=self.min_vals.dot(other.min_vals),
                    max_vals=self.max_vals.dot(other.max_vals),
                    entities=self.entities
                )
            else:
                raise NotImplementedError
        elif isinstance(other, GammaTensor):
            # Perhaps could do check for invalid arguments before conversion to GammaTensor?
            # return self.gamma.dot(other)
            raise NotImplementedError
        else:
            raise NotImplementedError

    def _object2bytes(self) -> bytes:
        schema = get_capnp_schema(schema_file="ndept.capnp")

        ndept_struct: CapnpModule = schema.NDEPT  # type: ignore
        ndept_msg = ndept_struct.new_message()
        # this is how we dispatch correct deserialization of bytes
        ndept_msg.magicHeader = serde_magic_header(type(self))

        # We always have FPT as the child of an NDEPT in the tensor chain.
        chunk_bytes(serialize(self.child, to_bytes=True), "child", ndept_msg)  # type: ignore

        ndept_msg.minVals = serialize(self.min_vals, to_bytes=True)
        ndept_msg.maxVals = serialize(self.max_vals, to_bytes=True)
        ndept_msg.dataSubjectsIndexed = capnp_serialize(
            self.entities.data_subjects_indexed
        )

        ndept_msg.oneHotLookup = capnp_serialize(
            liststrtonumpyutf8(self.entities.one_hot_lookup)
        )

        # to pack or not to pack?
        # to_bytes = ndept_msg.to_bytes()

        return ndept_msg.to_bytes_packed()

    @staticmethod
    def _bytes2object(buf: bytes) -> NDimEntityPhiTensor:
        schema = get_capnp_schema(schema_file="ndept.capnp")
        ndept_struct: CapnpModule = schema.NDEPT  # type: ignore
        # https://stackoverflow.com/questions/48458839/capnproto-maximum-filesize
        MAX_TRAVERSAL_LIMIT = 2**64 - 1
        # to pack or not to pack?
        # ndept_msg = ndept_struct.from_bytes(buf, traversal_limit_in_words=2 ** 64 - 1)
        ndept_msg = ndept_struct.from_bytes_packed(
            buf, traversal_limit_in_words=MAX_TRAVERSAL_LIMIT
        )

        child = deserialize(combine_bytes(ndept_msg.child), from_bytes=True)
        min_vals = deserialize(ndept_msg.minVals, from_bytes=True)
        max_vals = deserialize(ndept_msg.maxVals, from_bytes=True)
        data_subjects_indexed = capnp_deserialize(ndept_msg.dataSubjectsIndexed)
        one_hot_lookup = numpyutf8tolist(capnp_deserialize(ndept_msg.oneHotLookup))

        entity_list = DataSubjectList(one_hot_lookup, data_subjects_indexed)

        return NDimEntityPhiTensor(
            child=child, min_vals=min_vals, max_vals=max_vals, entities=entity_list
        )
