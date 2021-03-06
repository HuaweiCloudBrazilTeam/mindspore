/**
 * Copyright 2020 Huawei Technologies Co., Ltd
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "optimizer/irpass/arithmetic_simplify.h"

namespace mindspore {
namespace opt {
namespace irpass {
AnfNodePtr ArithmeticSimplify::operator()(const OptimizerPtr &, const AnfNodePtr &node) {
  PatternNode x, y, z, xs;
  PConstant one_(node, false, 1);
  PConstant one_scalar_(node, false, 1, true);
  PConstant zero_(node, false, 0);
  PConstant zero_scalar_(node, false, 0, true);
  PConstant const_(node);
  PConstant const_2(node);
  PConstant any_const(node);

  MATCH_REPLACE(node, x + zero_, x);                                                         // Add by zero
  MATCH_REPLACE(node, x + zero_scalar_, x);                                                  // Add by zero
  MATCH_REPLACE(node, PPrimitive(prim::kPrimScalarAdd, zero_scalar_, x), x);                 // Scalar Add by zero
  MATCH_REPLACE(node, PPrimitive(prim::kPrimScalarAdd, x, zero_scalar_), x);                 // Scalar Add by zero
  MATCH_REPLACE_IF(node, x * one_, any_const.WithValueOf(x), x.CheckFunc(IsVNode, node));    // Multiply by one
  MATCH_REPLACE(node, PPrimitive(prim::kPrimScalarMul, one_scalar_, x), x);                  // Scalar Mul by one
  MATCH_REPLACE(node, PPrimitive(prim::kPrimScalarMul, x, one_scalar_), x);                  // Scalar Mul by one
  MATCH_REPLACE(node, PPrimitive(prim::kPrimScalarMul, zero_scalar_, x), zero_.NewValue());  // Scalar Mul by zero
  MATCH_REPLACE(node, PPrimitive(prim::kPrimScalarMul, x, zero_scalar_), zero_.NewValue());  // Scalar Mul by zero

  // Prim Eliminate (identity)
  MATCH_REPLACE(node, PPrimitive(prim::kPrimIdentity, x), x);

  // ConstantDuplicateMul
  auto const_dup_lambda = [&node, &x, &const_, &const_2]() -> AnfNodePtr {
    auto new_mul_tensor = const_.MulByPatternConst(const_2, x.GetNode(node));
    auto mul_node = node->cast<CNodePtr>()->inputs()[0];
    if (new_mul_tensor == nullptr) {
      auto ttmul = NewCNode({mul_node, const_.GetNode(node), const_2.GetNode(node)}, node->func_graph());
      return NewCNode({mul_node, x.GetNode(node), ttmul}, node->func_graph());
    }
    return NewCNode({mul_node, x.GetNode(node), new_mul_tensor}, node->func_graph());
  };
  MATCH_REPLACE_LAMBDA(node, const_ * (const_2 * x), const_dup_lambda);

  if (node->func_graph() == nullptr) {
    return nullptr;
  }

  // OptUpdateZeroTensor
  MATCH_REPLACE(node, PPrimitive(prim::kPrimMomentum, PPrimitive(prim::kPrimZerosLike, x), y, z, xs),
                PPrimitive(prim::kPrimMakeTuple, z, y));

  // PowerOneEliminate
  MATCH_REPLACE(node, PPrimitive(prim::kPrimPow, x, one_scalar_), x);

  return nullptr;
}

AnfNodePtr ArithmeticSimplify2::operator()(const OptimizerPtr &, const AnfNodePtr &node) {
  PatternNode x, y;
  PConstant zero_(node, false, 0);

  MATCH_REPLACE(node, x * zero_, zero_);                                // Multiply by zero
  MATCH_REPLACE(node, x * PPrimitive(prim::kPrimZerosLike, y), zero_);  // Multiply by zero

  return nullptr;
}

// grad = AllReduce(grad) / worker_number
// grad = grad + weight * decy
// ->
// grad = grad + weight * decy
// grad = AllReduce(grad) / worker_number
// {prim::kPrimAddN, {prim::kPrimMakeTuple, {prim::kPrimMul, {prim::kPrimAllReduce, X}, Y}, Z}} ->
// {prim::kPrimMul, {prim::kPrimAllReduce, {prim::kPrimAddN,{prim::kPrimMakeTuple, Z, X}}}, Y}
AnfNodePtr AdjustAllReduceMulAdd::operator()(const OptimizerPtr &, const AnfNodePtr &node) {
  Reset();
  // {prim::kPrimAddN, Zs}
  if (!IsPrimitiveCNode(node, prim::kPrimAddN)) {
    return nullptr;
  }
  auto addn = node->cast<CNodePtr>();
  if (addn->size() != 2) {
    return nullptr;
  }
  AnfVisitor::Match(prim::kPrimMakeTuple, {IsNode, IsNode})(addn->input(1));
  if (x_ == nullptr || y_ == nullptr || z_ == nullptr || all_reduce_fg_ == nullptr) {
    return nullptr;
  }
  auto addn_maketuple = addn->input(1);

  auto fg = all_reduce_fg_;
  // addn inputs cross the graph, make the inputs same as allreduce node.
  if (z_->isa<CNode>() && fg != z_->func_graph()) {
    auto cnode_z = z_->cast<CNodePtr>();
    z_ = NewCNode(cnode_z->inputs(), fg);
  }

  auto addn_op_node = addn->input(0);
  auto make_tuple_op_node = addn->input(1)->cast<CNodePtr>()->input(0);

  AnfNodePtr tuple = NewCNode({make_tuple_op_node, z_, x_}, fg);
  AnfNodePtr add = NewCNode({addn_op_node, tuple}, fg);
  AnfNodePtr all_reduce = NewCNode({all_reduce_, add}, fg);
  AnfNodePtr mul = NewCNode({mul_, all_reduce, y_}, fg);
  ProcessDependEdge(fg, addn_maketuple, all_reduce);
  return mul;
}

void AdjustAllReduceMulAdd::ProcessDependEdge(const FuncGraphPtr &fg, const AnfNodePtr &addn_maketuple,
                                              const AnfNodePtr &new_node) {
  // If has dynamic loss scale.
  auto &users_map = fg->manager()->node_users();
  auto it = users_map.find(mul_cnode_);
  if (it != users_map.end()) {
    auto users = it->second;
    for (auto &user_pair : users) {
      auto node = user_pair.first;
      if (node != addn_maketuple) {
        if (IsPrimitiveCNode(node, prim::kPrimMakeTuple)) {
          fg->manager()->SetEdge(node, user_pair.second, new_node);
        }
      }
    }
  }
}

void AdjustAllReduceMulAdd::Visit(const AnfNodePtr &node) {
  if (level_ == 0) {
    level_ = 1;
    is_reduce_match_ = false;
    // {prim::kPrimMul, {prim::kPrimAllReduce, X}, Y}
    AnfVisitor::Match(prim::kPrimMul)(node);
    level_ = 0;
    if (is_reduce_match_) {
      mul_ = node->cast<CNodePtr>()->input(0);
      mul_cnode_ = node->cast<CNodePtr>();
      y_ = tmp_;
    } else {
      z_ = node;
    }
  }

  if (level_ == 1) {
    // {prim::kPrimAllReduce, X}
    if (IsPrimitiveCNode(node, prim::kPrimAllReduce)) {
      auto cnode = node->cast<CNodePtr>();
      if (cnode->size() > 1) {
        all_reduce_ = cnode->input(0);
        x_ = cnode->input(1);
        is_reduce_match_ = true;
        all_reduce_fg_ = cnode->func_graph();
      }
    } else {
      tmp_ = node;
    }
  }
}

void AdjustAllReduceMulAdd::Reset() {
  level_ = 0;
  is_reduce_match_ = false;
  x_ = nullptr;
  y_ = nullptr;
  z_ = nullptr;
  tmp_ = nullptr;
  all_reduce_fg_ = nullptr;
}

}  // namespace irpass
}  // namespace opt
}  // namespace mindspore
