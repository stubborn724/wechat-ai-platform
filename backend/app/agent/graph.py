"""LangGraph graph definition for the article generation pipeline."""

from langgraph.graph import END, StateGraph

from app.agent.nodes.content_node import generate_content_node
from app.agent.nodes.image_analysis_node import analyze_images_node
from app.agent.nodes.image_generation_node import generate_images_node
from app.agent.nodes.merge_node import merge_content_node
from app.agent.nodes.outline_node import generate_outline_node
from app.agent.nodes.title_node import generate_titles_node
from app.agent.state import ArticleGenState


def create_article_graph() -> StateGraph:
    """创建文章生成 LangGraph。

    Returns:
        编译后的 StateGraph，可直接以状态字典为输入进行 invoke / astream。
    """

    graph = StateGraph(ArticleGenState)

    # 注册节点 ———— 每个节点都是一个接受 state 并返回 dict 更新的函数
    graph.add_node("generate_titles", generate_titles_node)
    graph.add_node("generate_outline", generate_outline_node)
    graph.add_node("generate_content", generate_content_node)
    graph.add_node("analyze_images", analyze_images_node)
    graph.add_node("generate_images", generate_images_node)
    graph.add_node("merge_content", merge_content_node)

    # 线性流水线：标题 → 大纲 → 正文 → 图片分析 → 图片生成 → 合并
    graph.set_entry_point("generate_titles")
    graph.add_edge("generate_titles", "generate_outline")
    graph.add_edge("generate_outline", "generate_content")
    graph.add_edge("generate_content", "analyze_images")
    graph.add_edge("analyze_images", "generate_images")
    graph.add_edge("generate_images", "merge_content")
    graph.add_edge("merge_content", END)

    return graph.compile()
