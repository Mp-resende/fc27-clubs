from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    __allow_unmapped__ = True  # permite annotations sem Mapped[] no estilo Column clássico

