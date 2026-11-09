import opendssdirect as dss

dss.Text.Command("clear")
dss.Text.Command("new circuit.test basekv=11 pu=1.0 phases=3 bus1=source")
dss.Text.Command("new transformer.t1 phases=3 windings=2 buses=[source lv] conns=[delta wye] kvs=[11 0.415] kvas=[500 500] %r=1 xhl=4")
dss.Text.Command("new load.l1 bus1=lv phases=3 kv=0.415 kw=200 pf=0.95")
dss.Text.Command("set voltagebases=[11 0.415]")
dss.Text.Command("calcvoltagebases")
dss.Text.Command("solve")

print("Converged:", dss.Solution.Converged())
for node, v in zip(dss.Circuit.AllNodeNames(), dss.Circuit.AllBusMagPu()):
    print(f"{node:12s} {v:.4f} pu")
print("Total losses (kW):", round(dss.Circuit.Losses()[0] / 1000, 3))
