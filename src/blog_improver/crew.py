from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import ScrapeWebsiteTool, SerperDevTool
from crewai.agents.agent_builder.base_agent import BaseAgent
from typing import List

from blog_improver.llm import get_default_llm


@CrewBase
class BlogTopicStrategistCrew:
    """Crew 1: Analyzes the blog RSS feed and suggests 5 new article topics (in Chinese)."""

    agents_config = "config/topic_agents.yaml"
    tasks_config = "config/topic_tasks.yaml"

    agents: List[BaseAgent]
    tasks: List[Task]

    # ── Agents ────────────────────────────────────────────────────────────────

    @agent
    def blog_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["blog_analyst"],
            tools=[ScrapeWebsiteTool()],
            llm=get_default_llm(),
            verbose=True,
        )

    @agent
    def trend_researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["trend_researcher"],
            tools=[SerperDevTool()],
            llm=get_default_llm(),
            verbose=True,
        )

    @agent
    def content_strategist(self) -> Agent:
        return Agent(
            config=self.agents_config["content_strategist"],
            llm=get_default_llm(),
            verbose=True,
        )

    # ── Tasks ─────────────────────────────────────────────────────────────────

    @task
    def analyze_blog_task(self) -> Task:
        return Task(config=self.tasks_config["analyze_blog_task"])

    @task
    def research_trends_task(self) -> Task:
        return Task(config=self.tasks_config["research_trends_task"])

    @task
    def generate_suggestions_task(self) -> Task:
        return Task(
            config=self.tasks_config["generate_suggestions_task"],
            output_file="suggestions.md",
        )

    # ── Crew ──────────────────────────────────────────────────────────────────

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )


@CrewBase
class SEOReviewerCrew:
    """Crew 2: Scrapes a given blog post URL and produces a Chinese SEO + quality review."""

    agents_config = "config/review_agents.yaml"
    tasks_config = "config/review_tasks.yaml"

    agents: List[BaseAgent]
    tasks: List[Task]

    # ── Agents ────────────────────────────────────────────────────────────────

    @agent
    def article_reader(self) -> Agent:
        return Agent(
            config=self.agents_config["article_reader"],
            tools=[ScrapeWebsiteTool()],
            llm=get_default_llm(),
            verbose=True,
        )

    @agent
    def seo_specialist(self) -> Agent:
        return Agent(
            config=self.agents_config["seo_specialist"],
            llm=get_default_llm(),
            verbose=True,
        )

    @agent
    def technical_editor(self) -> Agent:
        return Agent(
            config=self.agents_config["technical_editor"],
            llm=get_default_llm(),
            verbose=True,
        )

    # ── Tasks ─────────────────────────────────────────────────────────────────

    @task
    def read_article_task(self) -> Task:
        return Task(config=self.tasks_config["read_article_task"])

    @task
    def seo_audit_task(self) -> Task:
        return Task(config=self.tasks_config["seo_audit_task"])

    @task
    def quality_review_task(self) -> Task:
        return Task(
            config=self.tasks_config["quality_review_task"],
            output_file="review.md",
        )

    # ── Crew ──────────────────────────────────────────────────────────────────

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
