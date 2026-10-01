# -*- coding: utf-8 -*-
"""回复层级构建与序列化服务（V3 回复层级）。

从 routes/posts.py 抽出（P1-6），原因：
  - 回复树的构建/序列化是纯领域逻辑，被 posts 与 replies 两个蓝图复用；
  - 原先放在 posts.py 会导致 replies.py 反向 import posts.py，形成隐式循环依赖；
  - 独立到 services/ 后，路由层只负责 HTTP 语义，领域逻辑集中可测。

对外约定（字段口径由 test_reply_hierarchy 锁定，不得变更）：
  - depth：展示深度（0 起，按 MAX_REPLY_DEPTH 收敛）
  - root_id：一级回复解析为自身 id，其余为话题根回复 id
  - reply_to_display_name：真实父回复的作者显示名
  - reply_count：直接子回复数
"""

import sqlalchemy as sa

from models import Reply, db


def _parse_dt(value):
    """将 datetime 转为 ISO 字符串，None 安全（与 posts._parse_dt 同口径）。"""
    from datetime import timezone

    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        # 如果数据库返回的是 naive datetime，附加 UTC 时区信息
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    except AttributeError:
        return str(value)


def reply_chain_info(reply, index):
    """一次回溯同时计算真实链信息。

    返回 (真实深度, 话题根回复 id)：
    - 真实深度：沿 parent_id 链向上回溯到一级回复的步数（一级回复 = 0）
    - 话题根 id：回溯到的那条一级回复的 id

    含环保护与悬空引用保护，避免脏数据导致死循环。
    """
    depth = 0
    cur = reply
    seen = set()
    while cur is not None and cur.parent_id:
        if cur.id in seen:
            break
        seen.add(cur.id)
        parent = index.get(cur.parent_id)
        if parent is None:
            break
        cur = parent
        depth += 1
    return depth, cur.id


def prepare_reply_structure(replies, max_depth):
    """预计算回复树的展示结构。

    replies 必须已按 created_at 升序（父回复必先于子回复出现，便于迭代求解）。

    返回 (index, depth_map, root_map, children_map, display_parent)：
    - index:          id → Reply ORM 对象
    - depth_map:      id → 展示深度（已按 max_depth 收敛，0/1/2…）
    - root_map:       id → 话题根回复 id（真实值，一级回复为 None）
    - children_map:   展示父 id → [展示子 id, ...]
    - display_parent: id → 展示父 id（None 表示展示为一级）
    """
    index = {r.id: r for r in replies}

    # 1) 真实链信息，用于话题根 id 与溢出判定
    chain = {r.id: reply_chain_info(r, index) for r in replies}

    # 2) 决定每个节点的"展示父节点"
    #    - 未超过深度上限：挂到真实父节点下
    #    - 超过深度上限：上提到父节点所在的展示层级，作为同级
    display_parent = {}
    for r in replies:
        if r.parent_id is None:
            display_parent[r.id] = None
            continue
        real_parent = index.get(r.parent_id)
        if real_parent is None:
            # 悬空引用（父回复已被单独删除等）：降级为一级回复
            display_parent[r.id] = None
            continue
        if chain[r.parent_id][0] + 1 <= max_depth:
            display_parent[r.id] = r.parent_id
        else:
            display_parent[r.id] = display_parent.get(r.parent_id)

    # 3) 展示深度（迭代求解，父节点必先于子节点）
    depth_map = {}
    for r in replies:
        parent_id = display_parent[r.id]
        depth_map[r.id] = 0 if parent_id is None else depth_map[parent_id] + 1

    # 4) 话题根 id（取真实值，一级回复为 None）
    root_map = {rid: info[1] for rid, info in chain.items()}

    # 5) 展示父子映射
    children_map = {}
    for r in replies:
        parent_id = display_parent[r.id]
        if parent_id is not None:
            children_map.setdefault(parent_id, []).append(r.id)

    return index, depth_map, root_map, children_map, display_parent


def reply_node_dict(reply, index, depth_map, root_map, children_map, post_author_id):
    """将单条 Reply 序列化为对外 JSON 节点（不含 children）。"""
    parent = index.get(reply.parent_id) if reply.parent_id else None
    author = reply.author
    return {
        "id": reply.id,
        "post_id": reply.post_id,
        "user_id": reply.user_id,
        "username": author.username if author else None,
        "nickname": author.nickname if author else None,
        "display_name": author.display_name if author else None,
        "avatar_url": author.avatar_url if author else None,
        "role": author.role if author else None,
        # 是否为楼主（帖子作者），供前端佩戴「楼主」徽标
        "is_author": reply.user_id == post_author_id,
        "content": reply.content,
        "created_at": _parse_dt(reply.created_at),
        "updated_at": _parse_dt(reply.updated_at),
        "parent_id": reply.parent_id,
        "root_id": root_map.get(reply.id),
        "depth": depth_map.get(reply.id, 0),
        # 被"真实"父回复人的显示名（即使展示上被拉平，引用对象仍准确）
        "reply_to_display_name": (
            parent.author.display_name if parent is not None and parent.author else None
        ),
        "reply_count": len(children_map.get(reply.id, [])),
    }


def build_reply_tree(replies, post_author_id, max_depth):
    """构建展示用嵌套回复树。

    返回 (tree, flat)：
    - tree: 嵌套结构，每个节点含 children（最多 max_depth+1 层）
    - flat: 扁平列表（按时间升序），字段同 tree 节点，用于兼容与统计
    """
    index, depth_map, root_map, children_map, display_parent = prepare_reply_structure(
        replies, max_depth
    )

    nodes = {
        r.id: reply_node_dict(
            r, index, depth_map, root_map, children_map, post_author_id
        )
        for r in replies
    }
    flat = [nodes[r.id] for r in replies]

    def attach_children(node):
        node["children"] = [nodes[kid] for kid in children_map.get(node["id"], [])]
        for child in node["children"]:
            attach_children(child)
        return node

    tree = [
        attach_children(nodes[r.id])
        for r in replies
        if display_parent[r.id] is None
    ]
    return tree, flat


def build_reply_node(reply, max_depth, post_author_id):
    """序列化单条回复（用于新建/编辑回复的响应体），含层级字段。

    性能说明（P0 优化）：原实现每次都对整帖回复做
    `filter_by(post_id).all()` 再全量建树，只为返回一条 —— 写路径复杂度
    随帖子回复数线性增长（O(n·depth)）。现改为定点查询：

    - depth：沿 parent_id 链最多回溯 max_depth+1 步（链长受展示深度限制）
    - root_id：一级回复取自身，其余沿用父回复的 root_id（或父自身）
    - reply_count：只 count 该条的直接子回复数
    - reply_to_display_name：只取直接父回复的作者显示名

    输出字段与旧实现逐项一致（已由 test_reply_hierarchy 覆盖）。
    """
    # ---- depth：与 prepare_reply_structure 的展示深度口径严格一致 ----
    # 展示父规则：若「真实父的展示深度 + 1」不超过 max_depth 则挂到真实父下；
    # 否则上提到「真实父的展示父」之下作为同级。展示深度 = 展示父深度 + 1。
    # 定点实现：沿 parent_id 链回溯收集祖先（由近到远），再由远到近递推复现，
    # 结果与全量建树一致，但复杂度只与链长相关（深度上限 3 层，故为常数级）。
    ancestors = []  # 由近到远：[直接父, 祖父, ...]
    if reply.parent_id:
        cur = reply
        seen = set()
        while cur is not None and cur.parent_id:
            if cur.id in seen:
                break
            seen.add(cur.id)
            parent = db.session.get(Reply, cur.parent_id)
            if parent is None:
                break
            ancestors.append(parent)
            cur = parent

    chain_up = list(reversed(ancestors))  # 由远到近（最靠近根的在前）
    disp_depth = {}   # reply_id -> 展示深度
    disp_parent = {}  # reply_id -> 展示父 id

    for idx, node in enumerate(chain_up):
        if idx == 0:
            # 链上最早（最靠近根）的祖先：视作其所在子树的展示根（深度 0）。
            # 注：若它自身仍有更远父节点，说明已被 max_depth 截断，
            # 在展示上也确实被上提为一级，此处一致。
            disp_parent[node.id] = None
            disp_depth[node.id] = 0
        else:
            real_parent_depth = disp_depth.get(chain_up[idx - 1].id, 0)
            if real_parent_depth + 1 <= max_depth:
                disp_parent[node.id] = chain_up[idx - 1].id
            else:
                disp_parent[node.id] = disp_parent.get(chain_up[idx - 1].id)
            dparent = disp_parent[node.id]
            disp_depth[node.id] = 0 if dparent is None else disp_depth.get(dparent, 0) + 1

    if not ancestors:
        depth = 0
    else:
        real_parent = ancestors[0]
        real_parent_depth = disp_depth.get(real_parent.id, 0)
        if real_parent_depth + 1 <= max_depth:
            depth = real_parent_depth + 1
        else:
            dparent = disp_parent.get(real_parent.id)
            depth = 0 if dparent is None else disp_depth.get(dparent, 0) + 1

    # root_id：一级回复（无父）在库中为 NULL，接口口径统一解析为其自身 id；
    # 其余沿用库中 root_id，缺失则回退到链上最早的一级回复
    if reply.parent_id is None:
        root_id = reply.id
    elif reply.root_id is not None:
        root_id = reply.root_id
    else:
        root_id = chain_up[0].id if chain_up else reply.id

    # 直接父回复的作者显示名（即使展示上被拉平，引用对象仍准确）
    reply_to_display_name = None
    if reply.parent_id:
        parent = db.session.get(Reply, reply.parent_id)
        if parent is not None:
            parent_author = parent.author
            reply_to_display_name = (
                parent_author.display_name if parent_author else None
            )

    # 直接子回复数（该条自身的 children 数量）
    reply_count = (
        db.session.query(sa.func.count())
        .select_from(Reply)
        .filter(Reply.parent_id == reply.id)
        .scalar()
        or 0
    )

    author = reply.author
    return {
        "id": reply.id,
        "post_id": reply.post_id,
        "user_id": reply.user_id,
        "username": author.username if author else None,
        "nickname": author.nickname if author else None,
        "display_name": author.display_name if author else None,
        "avatar_url": author.avatar_url if author else None,
        "role": author.role if author else None,
        "is_author": reply.user_id == post_author_id,
        "content": reply.content,
        "created_at": _parse_dt(reply.created_at),
        "updated_at": _parse_dt(reply.updated_at),
        "parent_id": reply.parent_id,
        "root_id": root_id,
        "depth": depth,
        "reply_to_display_name": reply_to_display_name,
        "reply_count": int(reply_count),
    }
