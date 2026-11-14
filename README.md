# PYTHON-DEVOPS-PIPELINE

Laboratório de Engenharia DevOps: uma aplicação Python passa por testes Pytest, é empacotada em Docker e o Jenkins executa esse fluxo sozinho.

Ao receber uma alteração, o pipeline:

1. Obtém o código do repositório.
2. Prepara um ambiente Python isolado.
3. Instala as dependências.
4. Executa os testes com Pytest.
5. Constrói a imagem Docker.
6. Executa a aplicação em um container.
7. Registra o resultado como **SUCCESS** ou **FAILURE**.

## Arquitetura

```text
Desenvolvedor (Cursor + Python + Git)
        |
        v
Repositório Git  --->  Jenkins
                         |-- Checkout
                         |-- Instalar dependências
                         |-- Testes Python / Pytest
                         |-- Build Docker
                         |-- Executar container
                         v
                    SUCCESS ou FAILURE
```

A aplicação é um release gate. Ela lê quatro sinais e só devolve SUCCESS quando todos passam:

| Sinal | Variável de ambiente | Padrão |
| --- | --- | --- |
| Dependências instaladas | `DEPENDENCIES_INSTALLED` | true |
| Testes aprovados | `TESTS_PASSED` | true |
| Imagem construída | `IMAGE_BUILT` | true |
| Container em execução | `CONTAINER_RUNNING` | true |

Qualquer valor `false`, `0`, `no` ou `off` reprova o gate e o processo termina com código 1. O modo `serve` publica um ledger HTTP para registrar, consultar e remover essas execuções.

## Estrutura

```text
app/main.py            Aplicação e ledger HTTP
tests/test_main.py     Testes Pytest do gate, do ledger e da API
requirements.txt       Dependências, incluindo o Pytest
Dockerfile             Imagem executável
docker-compose.yml     Sobe o ledger na porta 8000
Jenkinsfile            Pipeline declarativo
```

## Executar localmente

Na raiz do projeto:

```powershell
python -m pytest -v
docker build -t python-devops-pipeline:local .
docker run --rm python-devops-pipeline:local
```

A execução padrão imprime um JSON com `"status": "SUCCESS"` e termina com código 0.

Para comprovar a falha:

```powershell
docker run --rm -e TESTS_PASSED=false python-devops-pipeline:local
```

O JSON passa a `"status": "FAILURE"`, `failed_checks` aponta `tests_passed` e o código de saída é 1.

## Ledger de execuções

O Compose publica o serviço de dados em <http://127.0.0.1:8000>:

```powershell
docker compose up --build -d
```

- `GET /` mostra o ledger e o formulário.
- `POST /api/runs` registra `status`, `stage` e `summary`.
- `POST /api/runs/{id}/delete` remove um registro.
- `GET /api/runs` devolve o ledger em JSON.
- `GET /api/health` devolve a decisão atual do release gate.

`status` aceita apenas `SUCCESS` ou `FAILURE`.

## Pipeline Jenkins

O `Jenkinsfile` usa um agente Linux com `python3`, `venv` e Docker CLI apontando para o socket do daemon. O job precisa ser do tipo Pipeline from SCM, com o script em `Jenkinsfile`.

O parâmetro `RELEASE_MODE` controla a etapa final:

- `success`: o container recebe `TESTS_PASSED=true` e o pipeline termina SUCCESS.
- `failure`: o container recebe `TESTS_PASSED=false`, o processo sai com código 1 e o pipeline termina FAILURE.

Os relatórios ficam em `reports/pytest.xml` e `reports/container-result.json`, arquivados ao final de cada execução.

## Como ler o resultado

| Onde olhar | SUCCESS | FAILURE |
| --- | --- | --- |
| Console do Jenkins | `RESULTADO DO PIPELINE: SUCCESS` | `RESULTADO DO PIPELINE: FAILURE` |
| Código de saída do container | 0 | 1 |
| JSON do container | `"status": "SUCCESS"` | `"status": "FAILURE"` e a lista `failed_checks` |
| Pytest | todos os testes aprovados | a etapa de testes interrompe o pipeline antes do Docker |
