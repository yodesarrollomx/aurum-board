# Atribución de Plan confirmada en nube

El colector consulta los agregados publicados por Sala. Lee el recibo antes/después y compara SHA256 de los bytes exactos de Plan; exige contrato completo y lectura de menos de tres horas (tolerancia de cinco minutos al reloj futuro). No usa una credencial personal del Portero ni copia credenciales de Sala.

Cada grupo aporta su cantidad acreditada de leads/citas; no se expande a filas sintéticas. El histórico total se conserva como contexto en plan_lectura y no se suma a cada publicación. Un agregado sólo se cruza por su campaña/pieza real, con las convenciones existentes.

Ante rechazo, hash incorrecto, cambio concurrente o lectura antigua se conserva la atribución anterior y se señala medición incompleta. Arquitectura sigue con su token de máquina existente. PR/CI obligatorios para código; datos continúan por la rama pública de métricas/covers con recibo.

Reversión por PR y última versión confirmada. No borrar leads, estados, hojas, métricas o portadas. La fuente GAS original y los disparadores se documentan por separado.
