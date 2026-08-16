"""Formulário HTTPS mínimo para ações autenticadas por token."""

import html
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from app.authentication.models import CredentialAction
from app.authentication.passwords import PasswordPolicyError
from app.authentication.service import AuthenticationError, complete_action
from app.database.dependency import get_session

router = APIRouter(tags=["authentication"])


class CompleteCredentialAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[SecretStr, Field(min_length=1, max_length=128)]
    password: Annotated[SecretStr, Field(min_length=1, max_length=128)]
    password_confirmation: Annotated[SecretStr | None, Field(max_length=128)] = None


@router.get("/auth", include_in_schema=False)
def authentication_form() -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    page = _AUTH_PAGE.replace("{{NONCE}}", html.escape(nonce, quote=True))
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "connect-src 'self'; form-action 'self'"
            ),
        },
    )


@router.post("/auth/actions", operation_id="complete_credential_action")
def complete_credential_action(
    payload: CompleteCredentialAction,
    session: Session = Depends(get_session),
) -> JSONResponse:
    try:
        action = complete_action(
            session,
            raw_token=payload.token.get_secret_value(),
            password=payload.password.get_secret_value(),
            password_confirmation=(
                payload.password_confirmation.get_secret_value()
                if payload.password_confirmation is not None
                else None
            ),
        )
    except PasswordPolicyError as error:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"ok": False, "message": str(error)},
        )
    except AuthenticationError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "ok": False,
                "message": "Não foi possível concluir.\n\nSolicite um novo link pelo Telegram.",
            },
        )
    messages = {
        CredentialAction.LOGIN: "✅ Login concluído.\n\nVocê já pode voltar ao Telegram.",
        CredentialAction.SET_PASSWORD: (
            "✅ Senha criada.\n\nVolte ao Telegram e use /entrar."
        ),
        CredentialAction.CHANGE_PASSWORD: (
            "✅ Senha alterada.\n\nAs sessões anteriores foram encerradas.\n"
            "Volte ao Telegram e use /entrar novamente."
        ),
        CredentialAction.RECOVER_PASSWORD: (
            "✅ Senha redefinida.\n\nAs sessões anteriores foram encerradas.\n"
            "Volte ao Telegram e use /entrar novamente."
        ),
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"ok": True, "message": messages[action]},
    )


_AUTH_PAGE = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AIShoppingAgent — acesso seguro</title>
  <style nonce="{{NONCE}}">
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
      background: #0b1020; color: #f4f7ff; }
    main { width: min(28rem, calc(100% - 2rem)); padding: 2rem;
      background: #151c31; border: 1px solid #2c385b; border-radius: 1rem; }
    h1 { margin-top: 0; font-size: 1.5rem; }
    label { display: block; margin: 1rem 0 .4rem; }
    input, button { box-sizing: border-box; width: 100%; padding: .8rem;
      border-radius: .55rem; border: 1px solid #44527a; font: inherit; }
    input { background: #0f1629; color: inherit; }
    button { margin-top: 1.2rem; border: 0; background: #65a2ff;
      color: #07101e; font-weight: 700; cursor: pointer; }
    #message { min-height: 1.5rem; margin-bottom: 0; }
    .hidden { display: none; }
  </style>
</head>
<body>
<main>
  <h1>Acesso seguro</h1>
  <p>Este link é pessoal, descartável e expira em 10 minutos.</p>
  <form id="credential-form">
    <label for="password">Senha</label>
    <input id="password" type="password" minlength="8" maxlength="128"
      autocomplete="current-password" required>
    <small>Use de 8 a 128 caracteres, com maiúscula, minúscula, número e símbolo.</small>
    <section id="confirmation-block">
      <label for="confirmation">Confirmar senha</label>
      <input id="confirmation" type="password" minlength="8" maxlength="128"
        autocomplete="new-password">
    </section>
    <button type="submit">Continuar</button>
  </form>
  <p id="message" role="status" aria-live="polite"></p>
</main>
<script nonce="{{NONCE}}">
  const fragment = location.hash.slice(1);
  const separator = fragment.indexOf(':');
  const hint = separator > 0 ? fragment.slice(0, separator) : '';
  const token = separator > 0 ? fragment.slice(separator + 1) : '';
  history.replaceState(null, '', location.pathname);
  const form = document.querySelector('#credential-form');
  const confirmationBlock = document.querySelector('#confirmation-block');
  const confirmation = document.querySelector('#confirmation');
  const message = document.querySelector('#message');
  const login = hint === 'login';
  if (login) {
    confirmationBlock.classList.add('hidden');
    confirmation.required = false;
  } else {
    confirmation.required = true;
    document.querySelector('#password').autocomplete = 'new-password';
  }
  if (!token) {
    form.classList.add('hidden');
    message.textContent = 'Este link é inválido ou já expirou.';
  }
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const password = document.querySelector('#password').value;
    const body = {token, password};
    if (!login) body.password_confirmation = confirmation.value;
    const response = await fetch('/auth/actions', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body), credentials: 'omit'
    });
    const result = await response.json();
    message.textContent = result.message;
    if (result.ok) form.classList.add('hidden');
  });
</script>
</body>
</html>
"""
