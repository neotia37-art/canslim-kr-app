#!/usr/bin/env bash
# GitHub 커밋 + 푸시. 비밀키가 섞이지 않았는지 먼저 확인합니다.
set -e
MSG="${1:-update}"

if git ls-files --error-unmatch .streamlit/secrets.toml >/dev/null 2>&1; then
  echo "중단: secrets.toml이 git에 추적되고 있습니다. DART 키가 노출됩니다."
  echo "  git rm --cached .streamlit/secrets.toml"
  exit 1
fi

git add -A
git status --short
echo
read -p "위 내용을 커밋합니다. 계속할까요? [y/N] " ok
[[ "$ok" == "y" ]] || exit 0

git commit -m "$MSG"
git push
echo "완료. Streamlit Cloud가 자동으로 재배포합니다."
