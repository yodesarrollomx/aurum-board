# Board comercial online

El colector horario sigue usando las APIs Meta/QAA y mantiene el contrato honesto de Plan de Potencial: rechazo no es cero y una atribución sin lectura no se borra.

Código: Pages de main, revisado por PR y CI. Datos: metrics.json y covers en rama datos-online, exclusivamente agregados públicos. GitHub Actions puede escribir esa rama con su token nativo; no requiere Administration ni una GitHub App nueva. No contiene código ni registros de leads.

Cada ejecución recupera el último snapshot confirmado, adquiere el mutex y guarda un ZIP privado en release draft. Después publica datos validados en la rama, comprueba bytes Git y el recibo servido por raw.githubusercontent.com. Sólo entonces confirma la versión privada. Nunca publica los releases draft.

El navegador valida SHA256 de metrics.json contra version-online.json y conserva el último dato ante fallos. Las portadas locales se resuelven a la rama de datos. Un respaldo inicial mantiene su fecha y no se presenta como lectura actual. No se modifican las decisiones del editor ni se publican anuncios automáticamente.

Prueba 38138596609: almacenamiento privado confirmado; el token nativo recibió HTTP403 al intentar cambiar Source de Pages. Esa limitación se resuelve con una rama exclusiva de datos; la configuración de Pages se conserva.

Atlas CHG-SALA-ONLINE-20261011 / CTR-SALA-SNAPSHOT-ONLINE, commit 93afbea366c19872bfb855463afece2ac73eeb08. CI, publicación y recibo reales se documentan después de integrar. Rollback por PR conserva atribuciones, portadas y registros.
