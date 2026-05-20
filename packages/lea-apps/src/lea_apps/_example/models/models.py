"""示例App的数据库模型。"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from lea_server.database import Base


class ExampleResource(Base):
    __tablename__ = "example_resources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, default="")
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=func.now())
