# Arquitetura

O alvo é um monólito modular em FastAPI. Os módulos de missões, catálogo/coleta, preços, compra, eventos, Telegram e IA devem possuir fronteiras explícitas, mas ser implantados juntos no MVP.

PostgreSQL será a fonte transacional. Serviços externos serão acessados por adaptadores. O AI Provider Manager será um módulo transversal obrigatório para qualquer chamada de IA.

A implementação atual contém o esqueleto FastAPI, configuração tipada e um módulo isolado de saúde. Os módulos de domínio permanecem pendentes e serão introduzidos somente pelas tarefas correspondentes.
