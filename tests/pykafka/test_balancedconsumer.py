from __future__ import absolute_import
import mock
import unittest2
from uuid import uuid4

from pykafka.balancedconsumer import BalancedConsumer
from six.moves import range


class TestBalancedConsumer(unittest2.TestCase):
    def test_decide_partitions(self):
        for i in range(100):
            num_participants = i + 1
            num_partitions = 100 - i
            consumer_group = 'testgroup'

            topic = mock.Mock()
            topic.name = 'testtopic'

            topic.partitions = {}
            for k in range(num_partitions):
                part = mock.Mock()
                part.id = k
                part.topic = topic
                part.leader = mock.Mock()
                part.leader.id = k % num_participants
                topic.partitions[k] = part

            cluster = mock.MagicMock()
            zk = mock.MagicMock()
            cns = BalancedConsumer(topic, cluster, consumer_group,
                                   zookeeper=zk, auto_start=False)

            participants = ['test-debian:{}'.format(uuid4())
                            for i in range(num_participants - 1)]
            participants.append(cns._consumer_id)
            participants.sort()
            partitions = cns._decide_partitions(participants)

            remainder_ppc = num_partitions % num_participants
            idx = participants.index(cns._consumer_id)
            parts_per_consumer = num_partitions / num_participants
            num_parts = parts_per_consumer + (0 if (idx + 1 > remainder_ppc) else 1)

            self.assertEqual(len(partitions), num_parts)


if __name__ == "__main__":
    unittest2.main()
