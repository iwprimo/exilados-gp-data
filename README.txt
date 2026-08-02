EXILADOS GP — SINCRONIZAÇÃO AUTOMÁTICA VIA GITHUB
==================================================

POR QUE ESTA VERSÃO EXISTE
--------------------------
A hospedagem gamer.gd executa o PHP, mas bloqueia a conexão de saída para a
porta 50709. Por isso, o api.php recebe timeout.

Nesta solução, o GitHub Actions faz a consulta externa e publica somente um
JSON sanitizado. O index.html hospedado no gamer.gd lê esse JSON por HTTPS na
porta 443.

CONFIGURAÇÃO
------------
1. Crie um repositório PÚBLICO no GitHub chamado, por exemplo:
   exilados-gp-data

2. Envie para esse repositório estas pastas:
   .github/
   scripts/
   data/

3. No GitHub, abra:
   Actions > Atualizar resultados Exilados GP > Run workflow

4. Aguarde a execução concluir. O arquivo data/races.json será preenchido.

5. No index.html, localize:

   const DATA_ENDPOINT = "https://raw.githubusercontent.com/SEU_USUARIO/exilados-gp-data/main/data/races.json";

   Troque SEU_USUARIO pelo seu usuário real do GitHub. Caso use outro nome de
   repositório ou branch, ajuste também esses trechos.

6. Envie somente o index.html atualizado para o gamer.gd.
   O api.php antigo não é mais necessário.

ATUALIZAÇÃO
-----------
O workflow é executado automaticamente a cada 5 minutos. Ele só cria um novo
commit quando a lista ou o conteúdo das corridas mudar.

PRIVACIDADE
-----------
O sincronizador não publica os Steam GUIDs. Ele converte cada GUID em um
identificador hash estável, permitindo consolidar o mesmo piloto mesmo que o
nome de exibição mude.

TESTE
-----
Abra diretamente no navegador:

https://raw.githubusercontent.com/SEU_USUARIO/exilados-gp-data/main/data/races.json

O retorno correto terá "ok": true e uma lista "races" preenchida.
