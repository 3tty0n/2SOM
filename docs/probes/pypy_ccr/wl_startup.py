# Import-heavy startup workload (serverless / cold-start shape): import a batch
# of stdlib modules and do one trivial unit of work, then exit. Dominated by
# module init + the JIT warming on import machinery -- never amortized.
import json, decimal, collections, re, datetime, csv, hashlib, base64
import struct, random, math, functools, itertools, copy, bisect
import xml.etree.ElementTree as ET

d = {"a": [1, 2, 3], "b": {"c": 4}}
s = json.dumps(d)
h = hashlib.sha256(s.encode("utf-8") if hasattr(s, "encode") else s).hexdigest()
print h[:8]
