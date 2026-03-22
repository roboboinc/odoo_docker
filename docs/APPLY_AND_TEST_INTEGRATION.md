# Aplicar e testar a integração Odoo ↔ Receevi (Chatwoot)

## 1. Aplicar as modificações

### 1.1 Variáveis de ambiente (produção)

O `.env` já deve ter as variáveis de integração. Confirma que existem:

- `ODOO_URL`, `ODOO_DB=robobo`, `ODOO_USERNAME`, `ODOO_PASSWORD`
- `RECEEVI_URL=https://oc.linhafala.org.mz`, `RECEEVI_FRONTEND_URL`, `RECEEVI_API_KEY`, `RECEEVI_ACCOUNT_ID`

Preenche `RECEEVI_API_KEY` com o token do Chatwoot (Settings → Applications → New Access Token).

### 1.1.1 Base de dados Receevi (Digital Ocean)

Se o Receevi falhar com **`database "receevi_production" does not exist`**, a base ainda não foi criada no PostgreSQL. Cria-a assim:

**Opção A – Painel Digital Ocean**

1. Acede ao cluster PostgreSQL na Digital Ocean.
2. Abre a consola SQL ou “Connection” / “Databases”.
3. Cria uma nova base de dados chamada **`receevi_production`** (ou executa `CREATE DATABASE receevi_production;` se houver editor SQL).

**Opção B – Linha de comandos (psql)**

```bash
# Usa os valores do teu .env (host, porta 25060, user, password)
# Não coloques a password no repositório — exporta a partir do .env, ex.:
#   set -a && source .env && set +a
PGPASSWORD="$POSTGRES_PASSWORD" psql -h db-postgresql-xxxxx-xxxxxx.com -p port -U doadmin -d defaultdb -c "CREATE DATABASE receevi_production;"
```

Confirma que `POSTGRES_PASSWORD` (ou o nome que usas no `.env` para o Postgres) está definido no ambiente antes de correr o `psql`. Se o teu cluster usar outra base por defeito (ex.: `postgres`), troca `defaultdb` por essa.

Depois de criar a base, **corre as migrations** antes de arrancar o servidor web (senão ocorre `relation "installation_configs" does not exist`):

```bash
cd /root/odoo_docker
docker compose run --rm receevi-web bundle exec rails db:prepare
```

Isto cria todas as tabelas do Chatwoot na base `receevi_production`. Se usas **Docker Swarm**, faz o deploy do stack em seguida (secção 1.2). Não uses `docker compose up` para produção quando o stack estiver em Swarm.

### 1.2 Produção com Docker Swarm

Em produção usa-se **Docker Swarm**. Não corras `docker compose up -d` para os mesmos serviços — evita ter contentores em duplicado (Compose + Swarm).

**Primeira vez (ou após alterações ao código FastAPI):**

```bash
cd /root/odoo_docker

# 1) Garantir que o nó está em Swarm (se ainda não estiver)
docker swarm init

# 2) A rede traefik-network tem de existir (criada pelo stack do Traefik ou manualmente)
docker network create traefik-network 2>/dev/null || true

# 3) Construir a imagem da FastAPI (o stack deploy não faz build)
docker build -t fastapi-integration:latest ./fastapi

# 4) Fazer deploy (ou atualizar) o stack
docker stack deploy -c docker-compose.yml odoo
```

**Só para atualizar após mudanças no `.env` ou para reiniciar serviços:**

```bash
cd /root/odoo_docker
docker stack deploy -c docker-compose.yml odoo
```

**Comandos úteis:**

```bash
# Ver serviços do stack
docker stack services odoo

# Ver os contentores (tasks) de um serviço
docker service ps odoo_receevi-web

# Logs (no Swarm o nome do serviço é stack_serviço: odoo_receevi-web, odoo_fastapi, etc.)
docker service logs -f odoo_receevi-web
docker service logs -f odoo_fastapi

docker service logs odoo_receevi-sidekiq --tail 200

docker service logs -f odoo_receevi-sidekiq

#remove stack service
docker stack rm odoo
```

### 1.2.1 Primeiro utilizador (admin) no Receevi

Não há credenciais pré-definidas. Se só vês a página de **login** e ainda não criaste nenhum utilizador, abre diretamente o registo:

**https://oc.linhafala.org.mz/app/auth/signup**

Cria aí o primeiro utilizador (nome, email, nome da conta, password). Esse utilizador fica como super administrador. Depois usa o mesmo email e password na página de login.

**Alternativa – criar admin por Rails (quando não podes usar o signup):**

No Chatwoot o login em `/app/login` usa **User** + **Account** + **AccountUser** (o papel de administrador é por conta). Usa um container do serviço `receevi-web` (no Swarm o nome é dinâmico).

O bash interpreta `!` dentro de aspas duplas (history expansion), por isso o script Ruby deve ir **entre aspas simples** ou num ficheiro. Exemplo com **ficheiro** (recomendado):

```bash
CONTAINER=$(docker ps -q --filter name=odoo_receevi-web)

# Criar script temporário (evita problemas com ! e aspas no bash)
cat << 'RUBY' > /tmp/chatwoot_create_admin.rb
email = "edsondeceliotomasreginaldo@gmail.com"
user = User.find_by(email: email)
unless user
  user = User.create!(
    name: "Edson Tomas",
    email: email,
    password: "@Problematic842236",
    password_confirmation: "@Problematic842236",
    confirmed_at: Time.current
  )
end
account = Account.find_by(name: "Linha Fala") || Account.create!(name: "Linha Fala")
unless AccountUser.exists?(account: account, user: user)
  AccountUser.create!(account: account, user: user, role: :administrator)
end
puts "OK: " + user.email + " is admin of " + account.name
RUBY

docker cp /tmp/chatwoot_create_admin.rb $CONTAINER:/tmp/create_admin.rb
docker exec -it $CONTAINER bundle exec rails runner /tmp/create_admin.rb
```

Alternativa numa só linha (aspas simples em volta do Ruby para o bash não expandir `!`):

```bash
docker exec -it $CONTAINER bundle exec rails runner 'email = "edsondeceliotomasreginaldo@gmail.com"; user = User.find_by(email: email); user = User.create!(name: "Edson Tomas", email: email, password: "@Problematic842236", password_confirmation: "@Problematic842236", confirmed_at: Time.current) unless user; account = Account.find_by(name: "Linha Fala") || Account.create!(name: "Linha Fala"); AccountUser.create!(account: account, user: user, role: :administrator) unless AccountUser.exists?(account: account, user: user); puts "OK: " + user.email + " is admin of " + account.name'
```

Depois faz login em https://oc.linhafala.org.mz/app/login com esse email e password.

**Verificar se existe um utilizador (por email):**

```bash
CONTAINER=$(docker ps -q --filter name=odoo_receevi-web)
docker exec -it $CONTAINER bundle exec rails runner 'email = "edsondeceliotomasreginaldo@gmail.com"; u = User.find_by(email: email); if u; puts "User EXISTS: " + u.email + " (id=" + u.id.to_s + ")"; u.accounts.each { |a| puts "  account: " + a.name }; else; puts "User NOT FOUND: " + email; end'
```

**Listar todos os utilizadores:**

```bash
docker exec -it $CONTAINER bundle exec rails runner 'User.all.each { |u| puts u.id.to_s + " | " + u.email + " | " + u.name }'
```

Para criar um **Super Admin** (painel em https://oc.linhafala.org.mz/super_admin), no mesmo container. Usa aspas simples em volta do Ruby para o bash não interpretar `!`:

```bash
CONTAINER=$(docker ps -q --filter name=odoo_receevi-web)
docker exec -it $CONTAINER bundle exec rails runner 'SuperAdmin.create!(email: "teu@email.com", password: "TuaPassword1!", name: "Teu Nome")'
```

**Usar o mesmo email que o user normal (Edson Tomas)** para poder entrar em /super_admin com as mesmas credenciais:

```bash
CONTAINER=$(docker ps -q --filter name=odoo_receevi-web)
docker exec -it $CONTAINER bundle exec rails runner '
  email = "edsondeceliotomasreginaldo@gmail.com"
  pw    = "Admin12345!"
  sa    = SuperAdmin.find_by(email: email)
  if sa
    sa.update!(password: pw, password_confirmation: pw)
    puts "OK: SuperAdmin password updated. Login at /super_admin with " + email + " / Admin12345!"
  else
    begin
      SuperAdmin.create!(email: email, password: pw, password_confirmation: pw, name: "Edson Tomas")
      puts "OK: SuperAdmin created. Login at /super_admin with " + email + " / Admin12345!"
    rescue => e
      puts "Error: " + e.message
    end
  end
'
```

Se der "Email has already been taken", o Chatwoot pode não permitir o mesmo email em User e SuperAdmin; nesse caso usa outro email só para SuperAdmin (ex.: superadmin@linhafala.org.mz). Depois acede a https://oc.linhafala.org.mz/super_admin.

**Se der "Email has already been taken"** ao criar, pode ser que o email já exista como **User** (e a validação impeça repetir). **Se der "undefined method for nil"** ao fazer update, não existe SuperAdmin com esse email. Listar SuperAdmins:

```bash
docker exec -it $CONTAINER bundle exec rails runner 'SuperAdmin.all.each { |s| puts s.id.to_s + " | " + s.email + " | " + s.name }'
```

**Criar ou atualizar password (seguro):** cria se não existir, atualiza password se existir:

```bash
docker exec -it $CONTAINER bundle exec rails runner 'email = "edsondeceliotomasreginaldo@gmail.com"; sa = SuperAdmin.find_by(email: email); if sa; sa.update!(password: "@Problematic842236", password_confirmation: "@Problematic842236"); puts "Password updated for " + sa.email; else; SuperAdmin.create!(email: email, password: "@Problematic842236", password_confirmation: "@Problematic842236", name: "Edson Tomas"); puts "SuperAdmin created for " + email; end'
```

**Verificar e corrigir password (401 sem Rack::Attack):** Se o login continua a dar 401, a password na BD pode não coincidir com a que introduces. Neste script troca EMAIL e PASSWORD pelos valores que queres usar no login; o script atualiza e confirma.
   ```bash
   CONTAINER=$(docker ps -q --filter name=odoo_receevi-web)
   docker exec -it $CONTAINER bundle exec rails runner '
   email = "superadmin@linhafala.org.mz"
   pw    = "Admin12345!"
   sa    = SuperAdmin.find_by(email: email)
   if sa.nil?
     SuperAdmin.create!(email: email, password: pw, password_confirmation: pw, name: "Super Admin")
     puts "Created SuperAdmin with email=#{email} password=#{pw}"
   else
     sa.update!(password: pw, password_confirmation: pw)
     ok = sa.valid_password?(pw)
     puts ok ? "OK: use email=#{email} password=#{pw} to login" : "ERROR: valid_password failed"
   end
   '
   ```
   Depois faz login em https://oc.linhafala.org.mz/super_admin com **exatamente** esse email e password (copia/cola para evitar erros).

**Redefinir password de um SuperAdmin existente** (ex.: quando o login em /super_admin devolve sempre à mesma página):

```bash
# Trocar PASSWORD e EMAIL pelos valores desejados
docker exec -it $CONTAINER bundle exec rails runner 'sa = SuperAdmin.find_by(email: "donthimas54@gmail.com"); sa.update!(password: "NovaPassword123!", password_confirmation: "NovaPassword123!"); puts "OK: password updated for " + sa.email'
```

**Login em /super_admin só devolve à mesma página (sem mensagem de erro)**  
Problema conhecido no Chatwoot: a sessão não é criada (ex.: atrás de proxy ou password com caracteres especiais guardada mal pela consola).

1. **Definir `FRONTEND_URL`** no `.env` (igual ao domínio HTTPS) e reiniciar o Receevi:
   ```bash
   # No .env:
   FRONTEND_URL=https://oc.linhafala.org.mz
   # Depois (Swarm):
   docker stack deploy -c docker-compose.yml odoo
   ```

2. **Atualizar a password do SuperAdmin existente** (evita apagar; apagar pode falhar se faltar migration). O Chatwoot exige **pelo menos 1 carácter especial** na password (ex.: `!@#$%`):
   ```bash
   CONTAINER=$(docker ps -q --filter name=odoo_receevi-web)
   docker exec -it $CONTAINER bundle exec rails runner 'sa = SuperAdmin.find_by(email: "donthimas54@gmail.com"); sa.update!(password: "Admin12345!", password_confirmation: "Admin12345!"); puts "OK: password updated"'
   ```
   Faz login em https://oc.linhafala.org.mz/super_admin com esse email e `Admin12345!`.

**Password correta mas volta à página de login sem mensagem:** O login é aceite mas a **sessão não persiste** — o cookie de sessão não fica Secure atrás do proxy HTTPS. No `.env` define `FORCE_SSL=true` (o Traefik já envia `X-Forwarded-Proto: https` via middleware sslheader), faz redeploy e tenta de novo. Se aparecer redirect loop ou falha em WebSockets, remove `FORCE_SSL`.

**401 + "[Rack::Attack][Blocked]" nos logs:** O Chatwoot (Rack::Attack) está a bloquear o IP após várias tentativas — **não é o Traefik**. No mobile o IP é outro, por isso a whitelist pode não chegar. Opções:
   - **Desativar Rack::Attack** (funciona em qualquer dispositivo): no `.env` definir `ENABLE_RACK_ATTACK=false` e fazer `docker stack deploy -c docker-compose.yml odoo`.
   - **Manter e whitelist vários IPs:** `RACK_ATTACK_ALLOWED_IPS=197.249.107.131,IP_DO_MOBILE,...` (IPs separados por vírgula).

3. **Se der erro ao apagar SuperAdmin** (ex.: `column csat_survey_responses.review_notes_updated_by_id does not exist`): a base está desatualizada. Correr migrations e depois repetir:
   ```bash
   docker exec -it $CONTAINER bundle exec rails db:migrate
   docker stack deploy -c docker-compose.yml odoo
   ```
   Só depois usar o passo 2 (atualizar password) ou, se precisares, apagar e criar novo SuperAdmin.

### 1.3 Atualizar o módulo Odoo (addon)

O addon `linhafala_omniChannel_integration` teve os paths dos assets corrigidos no `__manifest__.py`. Para aplicar:

**Opção A – Pela interface Odoo**

1. Entra em https://odoo.robobo.org (base **robobo**).
2. Ativa o modo desenvolvedor: Settings → ativar "Developer mode".
3. Apps → procura "Linha Fala Chatwoot Integration" (ou o nome do módulo).
4. Clica em "Upgrade" (ou "Actualizar") para recarregar o módulo e os assets.

**Opção B – Pela linha de comandos**

```bash
# Com Docker Swarm: descobre o container Odoo (o nome muda em cada deploy)
CONTAINER=$(docker ps -q --filter name=odoo_odoo)
docker exec -it $CONTAINER odoo -d robobo -u linhafala_omniChannel_integration --stop-after-init

# Com Docker Compose (desenvolvimento/local):
docker compose exec odoo odoo -d robobo -u linhafala_omniChannel_integration --stop-after-init
```

Reinicia o serviço Odoo depois da actualização (Swarm: `docker stack deploy -c docker-compose.yml odoo`).

---

## 2. Webhook no Chatwoot (Receevi)

O Receevi/Chatwoot deve enviar os eventos de mensagens para o FastAPI. O endpoint é:

### URL do webhook (produção)

```
https://api.linhafala.org.mz/webhooks/receevi
```

### Onde configurar

1. Entra no Receevi: **https://oc.linhafala.org.mz**
2. Faz login como administrador.
3. **Settings** (Configurações) → **Integrations** (Integrações) → **Webhooks**.
4. Adiciona um webhook:
   - **URL:** `https://api.linhafala.org.mz/webhooks/receevi`
   - **Eventos:** selecciona **message_created** (mensagens criadas – entrantes e respostas).
5. Guarda.

**Se no Odoo não aparece nada (lista "Chatwoot por inbox" vazia)** embora as mensagens apareçam no Receevi:

1. **Webhook configurado no Receevi?** Sem isto o Receevi não chama a FastAPI. Em https://oc.linhafala.org.mz → **Settings** → **Integrations** → **Webhooks**: adiciona um webhook com **URL** `https://api.linhafala.org.mz/webhooks/receevi` e evento **message_created**. Guarda.
2. **FastAPI a receber pedidos?** Envia uma mensagem no widget (ou inbox) e logo a seguir vê os logs da FastAPI:  
   `docker service logs odoo_fastapi --tail 50`  
   Deves ver um `POST /webhooks/receevi` e resposta 200. Se não aparecer nenhum POST, o webhook no Receevi não está configurado ou o evento não está seleccionado.
3. **Variáveis no .env** (para a FastAPI falar com o Odoo): `ODOO_URL`, `ODOO_DB=robobo`, `ODOO_USERNAME`, `ODOO_PASSWORD`. Reinicia a stack após alterar: `docker stack deploy -c docker-compose.yml odoo`.

### Verificar se o endpoint responde

No browser ou com `curl`:

```bash
curl -s https://api.linhafala.org.mz/webhooks/receevi
```

Resposta esperada (GET): `{"status":"ok","message":"Receevi webhook endpoint. Use POST with message_created events.",...}`

---

## 3. Testes rápidos

### 3.1 Health check da API

```bash
curl -s https://api.linhafala.org.mz/health
```

Confirma que `api`, `receevi` e `odoo` aparecem como "healthy" (ou que os erros fazem sentido, ex.: Receevi ainda a arrancar).

### 3.2 Fluxo Cliente → Receevi → FastAPI → Odoo

1. No Receevi, envia uma mensagem de teste num canal (ex.: WhatsApp ou Website).
2. No Odoo Discuss (odoo.robobo.org), verifica se aparece uma conversa/canal ligado a essa conversa do Receevi e se a mensagem do contacto aparece no Discuss.
3. Responde no Odoo Discuss; no Receevi deve aparecer a resposta do agente (o FastAPI faz polling Odoo → Receevi quando `RECEEVI_API_KEY` está definido).

### 3.3 Logs (se algo falhar)

```bash
# Logs do FastAPI
docker compose logs -f fastapi

# Logs do Receevi (web)
docker compose logs -f receevi-web
```

---

## 4. Erro 502 Bad Gateway em https://oc.linhafala.org.mz

Se aparecer **Bad Gateway** ao abrir o Receevi:

1. **Confirmar que as migrations foram aplicadas** (base com tabelas):
   ```bash
   docker compose run --rm receevi-web bundle exec rails db:prepare
   ```

2. **Garantir que o Receevi está em execução:**
   - **Com Docker Swarm (produção):** `docker stack deploy -c docker-compose.yml odoo`
   - Com Docker Compose (local): `docker compose up -d receevi-web receevi-sidekiq`

3. **Verificar estado do serviço (Swarm):**
   ```bash
   docker service ps odoo_receevi-web
   docker service logs odoo_receevi-web --tail 30
   ```
   O log deve mostrar `Listening on http://0.0.0.0:3000` sem erros de base de dados.

4. **Rede:** O Traefik e o `receevi-web` têm de estar na mesma rede (`traefik-network`). Com Swarm, o stack que define o Traefik e o stack do Odoo/Receevi devem usar a mesma rede externa.

---

## 5. Erro 500 "Something went wrong" (InstallationConfig / GlobalConfig)

Se o Receevi abre mas a página mostra **500 – Something went wrong** e nos logs aparece um stack trace em `installation_config.rb` (ex.: linha 30) e `GlobalConfig` / `ChatwootApp.chatwoot_cloud?`, a aplicação está a consultar a tabela `installation_configs` com um **default_scope** que usa uma coluna que não existe na base (ou a tabela está incompleta).

**Solução:**

1. **Correr todas as migrations** (garantir que a tabela tem o esquema esperado pelo image `chatwoot/chatwoot:latest`):
   ```bash
   docker compose run --rm receevi-web bundle exec rails db:migrate
   ```

2. **Se ainda falhar**, verificar o estado das migrations:
   ```bash
   docker compose run --rm receevi-web bundle exec rails db:migrate:status
   ```
   Todas devem estar `up`. Se alguma estiver `down`, o passo anterior deve tê-las aplicado.

3. **Reiniciar o Receevi** depois de migrar:
   - **Swarm:** `docker stack deploy -c docker-compose.yml odoo`
   - **Compose:** `docker compose up -d receevi-web receevi-sidekiq`

4. Se o erro persistir, pode haver desalinhamento entre a versão do image (ex.: `chatwoot/chatwoot:latest`) e o esquema já criado noutra versão. Nesse caso, confirma que estás a usar o mesmo image em todos os ambientes e que `db:prepare` ou `db:migrate` foi executado **depois** de criar a base `receevi_production`.

---

## 6. Postgres "remaining connection slots" (Sidekiq não envia webhooks)

Se nos logs do **Sidekiq** aparecer:

```text
connection to server at "161.35.198.219", port 25060 failed: FATAL: remaining connection slots are reserved for non-replication superuser connections
```

o PostgreSQL gerido (ex.: Digital Ocean) está **sem conexões disponíveis**. Os jobs em fila (incluindo o que envia o webhook para a FastAPI) falham ao obter conexão à base, por isso **o Receevi nunca chama** `https://api.linhafala.org.mz/webhooks/receevi` e o Odoo fica vazio.

**O que fazer:**

1. **Reduzir o uso de conexões no Receevi** (no `.env` ou nas variáveis do serviço):
   - Limitar o pool da Rails: `RAILS_MAX_THREADS=5` (receevi-web).
   - Limitar workers Sidekiq: no image Chatwoot o Sidekiq usa várias filas; não é trivial alterar sem Dockerfile. Alternativa: garantir que não há muitos replicas de `receevi-web` nem de `receevi-sidekiq` (replicas 1 para cada).

2. **Aumentar o limite no PostgreSQL** (no painel do fornecedor, ex. Digital Ocean): subir `max_connections` se o plano o permitir, ou mudar para um plano com mais conexões.

3. **Usar PgBouncer** à frente do PostgreSQL para fazer connection pooling (avançado; requer outro serviço).

4. **Reiniciar os serviços** após alterações: `docker stack deploy -c docker-compose.yml odoo`. Depois de o Postgres ter slots livres, os jobs do Sidekiq (incl. envio de webhooks) voltam a correr.

---

## 7. Curl ao webhook devolve resposta errada / Nenhum "Webhook received" nos logs FastAPI

Se ao fazer `curl -X POST https://api.linhafala.org.mz/webhooks/receevi ...` a resposta for algo como `"Ticket created successfully"` em vez de `"Message synced to Odoo Discuss"` ou `"status":"ok"`, **ou** se nos logs da FastAPI (`docker service logs odoo_fastapi`) nunca aparecer `"Webhook received from Receevi"` nem `"Request POST /webhooks/receevi"`, o pedido **não está a chegar ao serviço FastAPI** deste projeto. O Traefik (ou outro proxy) está a enviar o tráfego de `api.linhafala.org.mz` para outro serviço. **Solução:** usar o host dedicado de webhooks: em DNS, criar um A para `webhook.linhafala.org.mz` (mesmo IP de api) e no Receevi configurar a URL `https://webhook.linhafala.org.mz/webhooks/receevi`; o router Traefik para esse host aponta só para a FastAPI.

**O que verificar:**

1. **Apenas um router para api.linhafala.org.mz**  
   No Traefik (dashboard ou configuração), confirma que o host `api.linhafala.org.mz` está associado ao serviço **fastapi** (porta 8000) do stack **odoo**. Se existir outro stack ou outro router com a mesma regra `Host(\`api.linhafala.org.mz\`)`, o tráfego pode estar a ir para o outro serviço.

2. **Teste direto ao serviço (dentro da rede do Swarm)**  
   Noutro serviço do mesmo stack (ou num container temporário na rede `traefik-network`), faz:
   ```bash
   curl -s http://odoo_fastapi:8000/webhooks/receevi
   ```
   Deves obter `{"status":"ok","message":"Receevi webhook endpoint. Use POST..."}`. Assim confirmas que a FastAPI responde quando o pedido chega ao serviço certo.

3. **Testar a FastAPI dentro da rede (sem Traefik)**  
   No **host**, com a rede partilhada pelo stack odoo (ex.: `traefik-network` ou `odoo_default`):
   ```bash
   docker run --rm --network traefik-network alpine/curl -s http://odoo_fastapi:8000/webhooks/receevi
   ```
   Se der "no such network", lista as redes: `docker network ls` e usa a que o stack odoo usa (ex. `odoo_traefik-network` em Swarm).
   Resposta esperada: `{"status":"ok","message":"Receevi webhook endpoint. Use POST..."}`.  
   Se este curl funcionar mas o pedido público a `https://api.linhafala.org.mz` não chegar à FastAPI, o Traefik está a enviar esse host para outro backend.

   **Painel Traefik:** Se tiveres o dashboard (porta 8080), abre `http://<servidor>:8080` e verifica qual **Router** e **Service** estão associados ao host `api.linhafala.org.mz`. O service deve ser o do serviço FastAPI (ex. `odoo_fastapi`).

   **Traefik noutro stack:** O Traefik só descobre os serviços do stack odoo (e os labels como `Host(\`api.linhafala.org.mz\`)`) se estiver na **mesma rede** que eles. O stack odoo usa `traefik-network` (external). O stack onde corre o Traefik também deve usar essa rede:
   ```yaml
   services:
     traefik:
       networks:
         - traefik-network
   networks:
     traefik-network:
       external: true
   ```
   Redeploy do stack do Traefik depois de o anexar a `traefik-network`. Sem isto, o tráfego para `api.linhafala.org.mz` pode ir para outro backend ou não haver router para esse host.

4. **Log de todos os pedidos**  
   A FastAPI regista cada pedido com `Request <METHOD> <path>`. Depois de um redeploy, ao enviar uma mensagem no Receevi ou ao fazer `curl -X POST https://api.linhafala.org.mz/webhooks/receevi ...`, corre:
   ```bash
   docker service logs odoo_fastapi --tail 50
   ```
   - Se aparecer `Request POST /webhooks/receevi` → o tráfego está a chegar à FastAPI; o passo seguinte é ver se o sync com o Odoo falha (erros a seguir no log).
   - Se não aparecer nenhum `Request` para `/webhooks/receevi` → o host público está a ser servido por outro backend; corrige o encaminhamento no Traefik.

5. **Resposta continua a ser "Ticket created successfully"**  
   Isto indica que o contentor está a executar o **código antigo** (fluxo de tickets), não o novo (Discuss). O compose monta `./fastapi:/app`, portanto o que corre é o código que está **no servidor** em `~/odoo_docker/fastapi/`.  
   - **Se o código é actualizado por git no servidor:** faz `git pull` em `~/odoo_docker`, depois `docker service update --force odoo_fastapi` para reiniciar e carregar o novo `main.py`.  
   - **Se quiseres que a imagem seja a fonte de verdade** (sem depender do diretório no host): no compose de produção podes remover o volume `./fastapi:/app` (manter só `fastapi_data:/app/data`), construir sem cache e fazer redeploy:
   ```bash
   docker compose build --no-cache fastapi
   docker stack deploy -c docker-compose.yml odoo
   docker service update --force odoo_fastapi
   ```

---

## Resumo – URL para o Chatwoot

| Onde            | Valor |
|-----------------|--------|
| **Webhook URL** | `https://api.linhafala.org.mz/webhooks/receevi` ou **`https://webhook.linhafala.org.mz/webhooks/receevi`** (recomendado se api.* estiver em conflito com outro backend) |
| **Método**      | POST (evento `message_created`) |
| **GET (teste)** | `https://api.linhafala.org.mz/webhooks/receevi` ou `https://webhook.linhafala.org.mz/webhooks/receevi` |

Se o POST para `api.linhafala.org.mz` for sempre respondido por outro serviço (ex. "Ticket created"), configura no Receevi a URL **`https://webhook.linhafala.org.mz/webhooks/receevi`**. Cria um registo DNS **A** para `webhook.linhafala.org.mz` com o mesmo IP de `api.linhafala.org.mz`, faz redeploy do stack odoo e actualiza o webhook no Receevi para essa URL.
