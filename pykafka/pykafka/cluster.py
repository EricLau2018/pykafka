from __future__ import division
from __future__ import absolute_import

import logging
import time
import random
import six

from .broker import Broker
from .topic import Topic
from .protocol import ConsumerMetadataRequest, ConsumerMetadataResponse
from pykafka.exceptions import ConsumerCoordinatorNotAvailable
from six.moves import range


logger = logging.getLogger(__name__)


class Cluster(object):
    """Cluster implementation used to populate the KafkaClient."""

    def __init__(self,
                 hosts,
                 handler,
                 socket_timeout_ms=30 * 1000,
                 offsets_channel_socket_timeout_ms=10 * 1000,
                 socket_receive_buffer_bytes=64 * 1024,
                 exclude_internal_topics=True):
        self._seed_hosts = hosts
        self._socket_timeout_ms = socket_timeout_ms
        self._offsets_channel_socket_timeout_ms = offsets_channel_socket_timeout_ms
        self._handler = handler
        self._brokers = {}
        self._topics = {}
        self._socket_receive_buffer_bytes = socket_receive_buffer_bytes
        self._exclude_internal_topics = exclude_internal_topics
        self.update()

    @property
    def brokers(self):
        return self._brokers

    @property
    def topics(self):
        return self._topics

    @property
    def handler(self):
        return self._handler

    def _get_metadata(self):
        """Get fresh cluster metadata from a broker"""
        # Works either on existing brokers or seed_hosts list
        brokers = [b for b in list(self.brokers.values()) if b.connected]
        if brokers:
            for broker in brokers:
                response = broker.request_metadata()
                if response is not None:
                    return response
        else:  # try seed hosts
            brokers = self._seed_hosts.split(',')
            for broker_str in brokers:
                try:
                    h, p = broker_str.split(':')
                    broker = Broker(-1, h, p, self._handler,
                                    self._socket_timeout_ms,
                                    self._offsets_channel_socket_timeout_ms,
                                    buffer_size=self._socket_receive_buffer_bytes)
                    response = broker.request_metadata()
                    if response is not None:
                        return response
                except:
                    logger.exception('Unable to connect to broker %s',
                                     broker_str)
        # Couldn't connect anywhere. Raise an error.
        raise Exception('Unable to connect to a broker to fetch metadata.')

    def _update_brokers(self, broker_metadata):
        """Update brokers with fresh metadata.

        :param broker_metadata: Metadata for all brokers
        :type broker_metadata: Dict of `{name: metadata}` where `metadata is
            :class:`kafka.pykafka.protocol.BrokerMetadata`
        """
        # FIXME: A cluster with no topics returns no brokers in metadata
        # Remove old brokers
        removed = set(self._brokers.keys()) - set(broker_metadata.keys())
        for id_ in removed:
            logger.info('Removing broker %s', self._brokers[id_])
            self._brokers.pop(id_)
        # Add/update current brokers
        for id_, meta in six.iteritems(broker_metadata):
            if id_ not in self._brokers:
                logger.info('Discovered broker %s:%s', meta.host, meta.port)
                self._brokers[id_] = Broker.from_metadata(
                    meta, self._handler, self._socket_timeout_ms,
                    self._offsets_channel_socket_timeout_ms,
                    buffer_size=self._socket_receive_buffer_bytes
                )
            else:
                broker = self._brokers[id_]
                if meta.host == broker.host and meta.port == broker.port:
                    continue  # no changes
                # TODO: Can brokers update? Seems like a problem if so.
                #       Figure out and implement update/disconnect/reconnect if
                #       needed.
                raise Exception('Broker host/port change detected! %s', broker)

    def _update_topics(self, metadata):
        """Update topics with fresh metadata.

        :param metadata: Metadata for all topics
        :type metadata: Dict of `{name, metadata}` where `metadata` is
            :class:`kafka.pykafka.protocol.TopicMetadata`
        """
        # Remove old topics
        removed = set(self._topics.keys()) - set(metadata.keys())
        for name in removed:
            logger.info('Removing topic %s', self._topics[name])
            self._topics.pop(name)
        # Add/update partition information
        for name, meta in six.iteritems(metadata):
            if not self._should_exclude_topic(name):
                if name not in self._topics:
                    self._topics[name] = Topic(self, meta)
                    logger.info('Discovered topic %s', self._topics[name])
                else:
                    self._topics[name].update(meta)

    def _should_exclude_topic(self, topic_name):
        """Return a boolean indicating whether this topic should be exluded
        """
        if not self._exclude_internal_topics:
            return False
        return topic_name.startswith("__")

    def get_offset_manager(self, consumer_group):
        """Get the broker designated as the offset manager for this consumer
            group

        Based on Step 1 at https://cwiki.apache.org/confluence/display/KAFKA/Committing+and+fetching+consumer+offsets+in+Kafka

        :param consumer_group: the name of the consumer group
        :type consumer_group: str
        """
        # arbitrarily choose a broker, since this request can go to any
        broker = self.brokers[random.choice(list(self.brokers.keys()))]
        MAX_RETRIES = 3

        for i in range(MAX_RETRIES):
            if i > 0:
                logger.debug("Retrying")
            time.sleep(i)

            req = ConsumerMetadataRequest(consumer_group)
            future = broker.handler.request(req)
            try:
                res = future.get(ConsumerMetadataResponse)
            except ConsumerCoordinatorNotAvailable:
                logger.debug('Error discovering offset manager.')
                if i == MAX_RETRIES - 1:
                    raise
            else:
                coordinator = self.brokers.get(res.coordinator_id, None)
                if coordinator is None:
                    raise Exception('Coordinator broker with id {} not found'.format(res.coordinator_id))
                return coordinator

    def update(self):
        """Update known brokers and topics."""
        metadata = self._get_metadata()
        self._update_brokers(metadata.brokers)
        self._update_topics(metadata.topics)
        # N.B.: Partitions are updated as part of Topic updates.
