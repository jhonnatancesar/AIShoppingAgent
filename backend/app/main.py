"""Ponto de entrada da aplicação FastAPI."""

from fastapi import FastAPI


app = FastAPI(
    title="AIShoppingAgent",
    version="0.1.0",
    description="Agente inteligente de compras.",
)
