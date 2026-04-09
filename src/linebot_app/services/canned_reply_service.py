from __future__ import annotations


def build_self_intro_reply(*, model_name: str) -> str:
    resolved_model_name = model_name.strip() or "unknown-model"
    return (
        "我是這個 LINE Bot 的助理，主要用繁體中文協助日常問答、資訊整理、"
        "規劃建議與一般生活問題。\n\n"
        f"目前使用的聊天模型是：{resolved_model_name}。\n\n"
        "我比較擅長把問題講清楚、整理重點，必要時也會查詢即時資訊後再回覆來源。\n\n"
        "限制是：我不一定對每個領域都有完整資訊；遇到即時性很高的題目，"
        "會以可查到的來源為準；另外我不提供程式碼撰寫、修改或除錯協助。"
    )


def build_capability_inquiry_reply(*, incoming_text: str) -> str:
    normalized = "".join(incoming_text.lower().split())

    if any(token in normalized for token in ("上網", "查資料", "查詢", "browse", "search")):
        return (
            "我支援透過網路查詢，但會以可查到的可信來源為準。\n\n"
            "- 如果是天氣、股市、新聞這類即時資訊，我會優先查詢後再回答。\n"
            "- 如果是人物、公司、品牌、產品、制度、歷史背景、名詞解釋這類知識題，"
            "我也可以先查再整理重點。\n"
            "- 如果是比較冷門或來源稀少的主題，我可能只能整理目前查到的內容。\n"
            "- 如果查不到可信來源，我會直接告訴你，不會隨便編造。\n\n"
            "你可以直接問像是「台北明天天氣」、「今天台積電股價」"
            "或「LangGraph 是什麼」，我會先查再回。"
        )

    if any(token in normalized for token in ("會什麼", "能做什麼", "whatcanyoudo", "能力")):
        return (
            "我可以處理的不只是一兩種固定題型，而是偏向萬事通型助理。\n\n"
            "- 一般知識問答與重點整理\n"
            "- 即時資訊查詢與來源整理\n"
            "- 人物、公司、產品、制度、歷史背景、名詞解釋\n"
            "- 生活建議、比較分析、規劃與摘要\n"
            "- LINE 上傳圖片 OCR 與常見文件內容解析\n\n"
            "如果題目需要最新資訊，我會先查；如果是一般理解與整理題，我會直接幫你講清楚。"
        )

    return (
        "我是這個 LINE Bot 的助理，定位比較像萬事通型的資訊整理與問答助手。\n"
        "碰到需要最新資訊的題目，我會優先查證後再回答；如果資料不足，也會直接告訴你限制。"
    )
