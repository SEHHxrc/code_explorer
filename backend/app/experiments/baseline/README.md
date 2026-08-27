# TEMPORARY CONTROL GROUP — remove after the graph experiment

Everything in this directory exists only to produce the **without dependency graph** control group.
It is not a supported product mode and must never be imported by normal agent APIs.

If graph augmentation wins the experiment, delete this entire directory together with the baseline
creation path in `experiments/service.py`, its tests, and the comparison UI controls. Historical result
data may be exported independently.