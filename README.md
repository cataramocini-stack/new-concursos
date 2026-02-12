# 🎯 Sniper de Concursos SP

## 🚀 Visão Geral
Este projeto monitora o PCI Concursos para vagas abertas no estado de São Paulo e envia apenas novidades para o Discord via Webhook.

## 🧩 Funcionalidades Principais
- 🔎 Scraping focado em concursos de SP com fallback resiliente
- 🧹 Limpeza automática de concursos expirados
- 🏷️ Destaque de bancas (Vunesp, FGV, FCC, Instituto Mais)
- 💰 Sinalização de salários altos no Discord
- 📌 Captura de vagas, salários e link oficial quando disponíveis

## 🛠️ Como Funciona
- Acesse o PCI Concursos e busque a lista de concursos
- Extraia título, link, data de encerramento, vagas e salário
- Salve tudo em concursos.json
- Envie apenas os novos concursos para o Discord

## 🤖 Automação
- O GitHub Actions executa o bot automaticamente a cada 10 minutos

## 🔐 Configuração de Webhook
- Crie o segredo DISCORD_WEBHOOK no GitHub com a URL do seu webhook

## 📂 Estrutura do Projeto
- bot_concursos.py
- concursos.json
- .github/workflows/main.yml
