# Order events topic

- Today the topic carries 9,000 events per second on average, and once a day it peaks at three times the average
  for about an hour.
- Traffic doubles within the year. The partition count can't be changed after launch without downtime, so size it for
  that growth.
- One consumer instance processes 2,500 events per second. Each partition is read by one consumer instance at a time,
  and every event must be processed within seconds of arriving, peak hour included.
- The cluster has 6 brokers. It allows at most 2,000 partitions in total, and other topics already use 1,960. The
  partition count must be a multiple of the broker count, so partition leaders spread evenly.
- Events for the same order must be processed in the order they were produced. Events of different orders may
  interleave. One marketplace customer places about 30% of all orders.
- After a consumer bug, the team must be able to replay at least the last 3 days of events, and the consumers must
  work through that whole backlog within 24 hours while new events keep arriving at the average rate.
- Each event is 1 KB (1,000 bytes) on disk. The topic gets 16 TB (16 x 10^12 bytes) of broker disk in total, counting
  every replica. Size retention against the average rate after the year's growth.
- The topic must keep accepting writes with acks=all and min.insync.replicas=2 when any one broker is down.
