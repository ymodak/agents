# coding=utf-8
# Copyright 2018 The TF-Agents Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for tf_agents.bandits.agents.constraints."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import tensorflow as tf  # pylint: disable=g-explicit-tensorflow-version-import
from tf_agents.bandits.agents import constraints
from tf_agents.bandits.networks import global_and_arm_feature_network
from tf_agents.bandits.specs import utils as bandit_spec_utils
from tf_agents.networks import network
from tf_agents.specs import tensor_spec
from tf_agents.trajectories import time_step as ts
from tf_agents.utils import common


tf.compat.v1.enable_v2_behavior()


class GreaterThan2Constraint(constraints.BaseConstraint):

  def compute_action_feasibility(self, observation, actions=None):
    """Returns the probability of input actions being feasible."""
    if actions is None:
      actions = tf.range(self._action_spec.minimum, self._action_spec.maximum)
    feasibility_prob = tf.cast(tf.greater(actions, 2), tf.float32)
    return feasibility_prob


class BaseConstraintTest(tf.test.TestCase):

  def testSimpleCase(self):
    obs_spec = tensor_spec.TensorSpec([2], tf.float32)
    time_step_spec = ts.time_step_spec(obs_spec)
    action_spec = tensor_spec.BoundedTensorSpec(
        dtype=tf.int32, shape=(), minimum=0, maximum=5)
    gt2c = GreaterThan2Constraint(time_step_spec, action_spec)
    feasibility_prob = gt2c.compute_action_feasibility(observation=None)
    self.assertAllEqual([0, 0, 0, 1, 1], self.evaluate(feasibility_prob))


class DummyNet(network.Network):

  def __init__(self, unused_observation_spec, action_spec, name=None):
    super(DummyNet, self).__init__(
        unused_observation_spec, state_spec=(), name=name)
    action_spec = tf.nest.flatten(action_spec)[0]
    num_actions = action_spec.maximum - action_spec.minimum + 1

    # Store custom layers that can be serialized through the Checkpointable API.
    self._dummy_layers = [
        tf.keras.layers.Dense(
            num_actions,
            kernel_initializer=tf.compat.v1.initializers.constant(
                [[1, 1.5, 2],
                 [1, 1.5, 4]]),
            bias_initializer=tf.compat.v1.initializers.constant(
                [[1], [1], [-10]]))
    ]

  def call(self, inputs, step_type=None, network_state=()):
    del step_type
    inputs = tf.cast(inputs, tf.float32)
    for layer in self._dummy_layers:
      inputs = layer(inputs)
    return inputs, network_state


class NeuralConstraintTest(tf.test.TestCase):

  def setUp(self):
    super(NeuralConstraintTest, self).setUp()
    tf.compat.v1.enable_resource_variables()
    self._obs_spec = tensor_spec.TensorSpec([2], tf.float32)
    self._time_step_spec = ts.time_step_spec(self._obs_spec)
    self._action_spec = tensor_spec.BoundedTensorSpec(
        dtype=tf.int32, shape=(), minimum=0, maximum=2)
    self._observation_spec = self._time_step_spec.observation

  def testCreateConstraint(self):
    constraint_net = DummyNet(self._observation_spec, self._action_spec)
    constraints.NeuralConstraint(
        self._time_step_spec,
        self._action_spec,
        constraint_network=constraint_net)

  def testInitializeConstraint(self):
    constraint_net = DummyNet(self._observation_spec, self._action_spec)
    neural_constraint = constraints.NeuralConstraint(
        self._time_step_spec,
        self._action_spec,
        constraint_network=constraint_net)
    init_op = neural_constraint.initialize()
    if not tf.executing_eagerly():
      with self.cached_session() as sess:
        common.initialize_uninitialized_variables(sess)
        self.assertIsNone(sess.run(init_op))

  def testComputeLoss(self):
    constraint_net = DummyNet(self._observation_spec, self._action_spec)
    observations = tf.constant([[1, 2], [3, 4]], dtype=tf.float32)
    actions = tf.constant([0, 1], dtype=tf.int32)
    rewards = tf.constant([0.5, 3.0], dtype=tf.float32)

    neural_constraint = constraints.NeuralConstraint(
        self._time_step_spec,
        self._action_spec,
        constraint_network=constraint_net)
    init_op = neural_constraint.initialize()
    if not tf.executing_eagerly():
      with self.cached_session() as sess:
        common.initialize_uninitialized_variables(sess)
        self.assertIsNone(sess.run(init_op))
    loss = neural_constraint.compute_loss(
        observations,
        actions,
        rewards)
    self.assertAllClose(self.evaluate(loss), 42.25)

  def testComputeLossWithArmFeatures(self):
    obs_spec = bandit_spec_utils.create_per_arm_observation_spec(
        global_dim=2, per_arm_dim=3, num_actions=3)
    time_step_spec = ts.time_step_spec(obs_spec)
    constraint_net = (
        global_and_arm_feature_network.create_feed_forward_common_tower_network(
            obs_spec,
            global_layers=(4,),
            arm_layers=(4,),
            common_layers=(4,)))
    neural_constraint = constraints.NeuralConstraint(
        time_step_spec,
        self._action_spec,
        constraint_network=constraint_net)

    observations = {
        bandit_spec_utils.GLOBAL_FEATURE_KEY:
            tf.constant([[1, 2], [3, 4]], dtype=tf.float32),
        bandit_spec_utils.PER_ARM_FEATURE_KEY:
            tf.cast(
                tf.reshape(tf.range(18), shape=[2, 3, 3]), dtype=tf.float32)
    }
    actions = tf.constant([0, 1], dtype=tf.int32)
    rewards = tf.constant([0.5, 3.0], dtype=tf.float32)

    init_op = neural_constraint.initialize()
    if not tf.executing_eagerly():
      with self.cached_session() as sess:
        common.initialize_uninitialized_variables(sess)
        self.assertIsNone(sess.run(init_op))
    loss = neural_constraint.compute_loss(
        observations,
        actions,
        rewards)
    self.assertGreater(self.evaluate(loss), 0.0)

  def testComputeActionFeasibility(self):
    constraint_net = DummyNet(self._observation_spec, self._action_spec)

    neural_constraint = constraints.NeuralConstraint(
        self._time_step_spec,
        self._action_spec,
        constraint_network=constraint_net)
    init_op = neural_constraint.initialize()
    if not tf.executing_eagerly():
      with self.cached_session() as sess:
        common.initialize_uninitialized_variables(sess)
        self.assertIsNone(sess.run(init_op))

    observation = tf.constant([[1, 2], [3, 4]], dtype=tf.float32)
    feasibility_prob = neural_constraint.compute_action_feasibility(
        observation)
    self.assertAllClose(self.evaluate(feasibility_prob), np.ones([2, 3]))


class QuantileConstraintTest(tf.test.TestCase):

  def setUp(self):
    super(QuantileConstraintTest, self).setUp()
    tf.compat.v1.enable_resource_variables()
    self._obs_spec = tensor_spec.TensorSpec([2], tf.float32)
    self._time_step_spec = ts.time_step_spec(self._obs_spec)
    self._action_spec = tensor_spec.BoundedTensorSpec(
        dtype=tf.int32, shape=(), minimum=0, maximum=2)
    self._observation_spec = self._time_step_spec.observation

  def testCreateConstraint(self):
    constraint_net = DummyNet(self._observation_spec, self._action_spec)
    constraints.QuantileConstraint(
        self._time_step_spec,
        self._action_spec,
        constraint_network=constraint_net)

  def testComputeActionFeasibility(self):
    constraint_net = DummyNet(self._observation_spec, self._action_spec)

    quantile_constraint = constraints.QuantileConstraint(
        self._time_step_spec,
        self._action_spec,
        constraint_network=constraint_net)
    init_op = quantile_constraint.initialize()
    if not tf.executing_eagerly():
      with self.cached_session() as sess:
        common.initialize_uninitialized_variables(sess)
        self.assertIsNone(sess.run(init_op))

    observation = tf.constant([[1, 2], [3, 4]], dtype=tf.float32)
    feasibility_prob = quantile_constraint.compute_action_feasibility(
        observation)
    self.assertAllGreaterEqual(self.evaluate(feasibility_prob), 0.0)
    self.assertAllLessEqual(self.evaluate(feasibility_prob), 1.0)

if __name__ == '__main__':
  tf.test.main()
