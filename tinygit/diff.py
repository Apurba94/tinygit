"""Line diffs with Myers' algorithm, unified output, and three-way merge."""


def myers(a, b):
    """Return a shortest edit script as a list of (op, line): op is ' ', '-' or '+'."""
    n, m = len(a), len(b)
    limit = n + m
    v = {1: 0}
    trace = []
    for d in range(limit + 1):
        trace.append(dict(v))
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v.get(k - 1, -1) < v.get(k + 1, -1)):
                x = v.get(k + 1, 0)
            else:
                x = v.get(k - 1, 0) + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[k] = x
            if x >= n and y >= m:
                return _backtrack(a, b, trace)
    return []


def _backtrack(a, b, trace):
    x, y = len(a), len(b)
    out = []
    for d in range(len(trace) - 1, -1, -1):
        v = trace[d]
        k = x - y
        if k == -d or (k != d and v.get(k - 1, -1) < v.get(k + 1, -1)):
            prev_k = k + 1
        else:
            prev_k = k - 1
        prev_x = v.get(prev_k, 0)
        prev_y = prev_x - prev_k
        while x > prev_x and y > prev_y:
            x -= 1
            y -= 1
            out.append((" ", a[x]))
        if d > 0:
            if x == prev_x:
                y -= 1
                out.append(("+", b[y]))
            else:
                x -= 1
                out.append(("-", a[x]))
        x, y = prev_x, prev_y
    out.reverse()
    return out


def unified(a, b, name_a="a", name_b="b", context=3):
    """A patch in the standard unified format, as a list of lines."""
    script = myers(a, b)
    changes = [i for i, (op, _) in enumerate(script) if op != " "]
    if not changes:
        return []
    groups, start, end = [], changes[0], changes[0]
    for i in changes[1:]:
        if i - end > context * 2:
            groups.append((start, end))
            start = i
        end = i
    groups.append((start, end))
    out = [f"--- {name_a}", f"+++ {name_b}"]
    for start, end in groups:
        lo, hi = max(0, start - context), min(len(script), end + context + 1)
        before_a = sum(1 for op, _ in script[:lo] if op != "+")
        before_b = sum(1 for op, _ in script[:lo] if op != "-")
        part = script[lo:hi]
        count_a = sum(1 for op, _ in part if op != "+")
        count_b = sum(1 for op, _ in part if op != "-")
        out.append(f"@@ -{before_a + (1 if count_a else 0)},{count_a} +{before_b + (1 if count_b else 0)},{count_b} @@")
        out.extend(op + line for op, line in part)
    return out


def apply_patch(a, patch):
    """Apply a unified patch to a list of lines. Used to prove patches are correct."""
    out, pos = [], 0
    i = 2
    while i < len(patch):
        head = patch[i]
        start = int(head.split()[1].split(",")[0][1:])
        count = int(head.split()[1].split(",")[1])
        begin = start - 1 if count else start
        out.extend(a[pos:begin])
        pos = begin
        i += 1
        while i < len(patch) and not patch[i].startswith("@@"):
            op, line = patch[i][0], patch[i][1:]
            if op != "+":
                if a[pos] != line:
                    raise ValueError(f"patch does not match at line {pos + 1}")
                pos += 1
            if op != "-":
                out.append(line)
            i += 1
    out.extend(a[pos:])
    return out


def _regions(base, side):
    """For each change in side, return (base start, base end, replacement lines)."""
    out, i, pending, start = [], 0, None, 0
    for op, line in myers(base, side):
        if op == " ":
            if pending is not None:
                out.append((start, i, pending))
                pending = None
            i += 1
        else:
            if pending is None:
                pending, start = [], i
            if op == "-":
                i += 1
            else:
                pending.append(line)
    if pending is not None:
        out.append((start, i, pending))
    return out


def merge3(base, ours, theirs, label_ours="ours", label_theirs="theirs"):
    """Three-way merge. Returns (lines, conflict count)."""
    a, b = _regions(base, ours), _regions(base, theirs)
    out, pos, conflicts = [], 0, 0
    i = j = 0
    while i < len(a) or j < len(b):
        if j >= len(b) or (i < len(a) and a[i][0] <= b[j][0]):
            lo, hi = a[i][:2]
        else:
            lo, hi = b[j][:2]
        group_a, group_b = [], []
        grew = True
        while grew:
            grew = False
            while i < len(a) and a[i][0] <= hi and (a[i][0] < hi or a[i][0] == lo or hi == lo):
                group_a.append(a[i])
                lo, hi = min(lo, a[i][0]), max(hi, a[i][1])
                i += 1
                grew = True
            while j < len(b) and b[j][0] <= hi and (b[j][0] < hi or b[j][0] == lo or hi == lo):
                group_b.append(b[j])
                lo, hi = min(lo, b[j][0]), max(hi, b[j][1])
                j += 1
                grew = True
        out.extend(base[pos:lo])
        pos = hi
        mine = _rebuild(base, lo, hi, group_a)
        other = _rebuild(base, lo, hi, group_b)
        if not group_b or mine == other:
            out.extend(mine)
        elif not group_a:
            out.extend(other)
        else:
            conflicts += 1
            out.append(f"<<<<<<< {label_ours}")
            out.extend(mine)
            out.append("=======")
            out.extend(other)
            out.append(f">>>>>>> {label_theirs}")
    out.extend(base[pos:])
    return out, conflicts


def _rebuild(base, lo, hi, group):
    out, pos = [], lo
    for start, end, lines in group:
        out.extend(base[pos:start])
        out.extend(lines)
        pos = end
    out.extend(base[pos:hi])
    return out
