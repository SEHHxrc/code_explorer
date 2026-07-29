# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, JSON, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./database.sqlite"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class ProjectModel(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(
        String, index=True, default="default_user"
    )  # 预留的用户隔离字段
    repo_url = Column(String, nullable=False)
    local_path = Column(String, nullable=False)
    file_tree = Column(JSON, nullable=True)  # 存储静态文件树
    created_at = Column(DateTime, default=datetime.now(timezone.utc))


def init_db():
    Base.metadata.create_all(bind=engine)