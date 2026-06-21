# 기여 가이드

## 개발 환경

```bash
git clone https://github.com/TaewonyNet/CerberusBeacon.git
cd CerberusBeacon
uv sync --extra all
uv run pytest tests/
```

## 변경 흐름

1. 이슈로 먼저 논의(버그·기능). 사소한 수정은 바로 PR 가능.
2. `main` 에서 브랜치 분기: `feat/...`, `fix/...`, `docs/...`
3. `uv run pytest tests/` 통과 확인 후 PR.
4. 한 PR에 하나의 기능 또는 수정.

## 주의

- `~/.cerberus/` 내용(OTP 시크릿, API 토큰)을 커밋에 포함하지 마세요.
- 새 의존성은 `pyproject.toml` 에 추가 후 `uv sync`.

## 라이선스

기여한 코드는 [MIT](LICENSE) 로 배포되는 데 동의하는 것으로 간주합니다.
