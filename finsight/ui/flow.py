"""工作台流程状态推导（纯函数，展示层不反向影响业务）。"""

STEPS = ["财务数据", "备忘录生成", "独立核验", "确认修订", "再核验"]


def workflow_states(data_loaded: bool, has_v1: bool, has_round1: bool,
                    has_round2: bool, error: bool = False) -> list[str]:
    """按业务事实推导五步状态（todo/active/done/error）。

    error=True 时把"当前推进中的步骤"标为 error，其余不变。
    """
    states = []
    # 1 财务数据
    states.append("done" if data_loaded else "todo")
    # 2 备忘录生成
    if has_v1:
        states.append("done")
    elif data_loaded:
        states.append("active")
    else:
        states.append("todo")
    # 3 独立核验
    if has_round1:
        states.append("done")
    elif has_v1:
        states.append("active")
    else:
        states.append("todo")
    # 4 确认修订
    if has_round2:
        states.append("done")
    elif has_round1:
        states.append("active")
    else:
        states.append("todo")
    # 5 再核验
    if has_round2:
        states.append("done")
    elif has_round1:
        states.append("todo")
    else:
        states.append("todo")

    if error:
        for i, s in enumerate(states):
            if s == "active":
                states[i] = "error"
                break
        else:
            states[0] = "error" if not data_loaded else states[0]
    return states
