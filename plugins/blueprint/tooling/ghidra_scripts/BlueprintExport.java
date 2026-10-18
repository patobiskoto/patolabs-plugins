// Batch export of one already-analysed program (PAT-260). Run by `analyzeHeadless -process -noanalysis`.
// Arguments (key=value): out=FILE include=REGEX max=N timeout=SECONDS data=REGEX xrefs=FILE swift=1
// swift=1 also reads the Swift type descriptors (__swift5_types) and their field descriptors.
// Writes one JSON document to `out`. Nothing is analysed here: the analysis was done once, by the import.
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.lang.Register;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.symbol.RefType;
import ghidra.program.model.mem.MemoryAccessException;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class BlueprintExport extends GhidraScript {
    private static String q(String s) {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) b.append(String.format("\\u%04x", (int) c)); else b.append(c);
            }
        }
        return b.append('"').toString();
    }


    // A 32-bit self-relative pointer stored at `at`: the address it designates, or null for 0.
    private Address rel(Address at) throws MemoryAccessException {
        int v = currentProgram.getMemory().getInt(at);
        return v == 0 ? null : at.add(v);
    }

    private String cstr(Address a) throws MemoryAccessException {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < 512; i++) {
            int c = currentProgram.getMemory().getByte(a.add(i)) & 0xff;
            if (c == 0) break;
            b.append((char) c);
        }
        return b.toString();
    }

    // Readable form of a Swift mangled type name: a direct symbolic reference (0x01) to a type descriptor becomes the
    // descriptor's name; any other symbolic reference stays visible as <ref:0xNN>.
    private String mangled(Address a) throws MemoryAccessException {
        StringBuilder b = new StringBuilder();
        for (int i = 0; i < 512; ) {
            int c = currentProgram.getMemory().getByte(a.add(i)) & 0xff;
            if (c == 0) break;
            if (c == 0x01 && i + 5 <= 512) {
                Address d = rel(a.add(i + 1));
                String n = d == null ? null : cstrOrNull(rel(d.add(8)));
                b.append(n == null ? "<ref:0x01>" : "[" + n + "]");
                i += 5;
            } else if (c >= 0x02 && c <= 0x1f) {
                b.append("<ref:0x").append(String.format("%02x", c)).append(">");
                i += 5;
            } else {
                b.append((char) c);
                i++;
            }
        }
        return b.toString();
    }

    private String cstrOrNull(Address a) throws MemoryAccessException {
        return a == null ? null : cstr(a);
    }

    // One __swift5_types slot: a relative pointer to a type context descriptor
    // (flags, parent, name, access function, fields). Kinds: 16 class, 17 struct, 18 enum.
    private String swiftType(Address slot) {
        try {
            int raw = currentProgram.getMemory().getInt(slot);
            if (raw == 0 || (raw & 1) != 0) return null;
            Address d = slot.add(raw);
            int flags = currentProgram.getMemory().getInt(d);
            int kind = flags & 0x1f;
            if (kind != 16 && kind != 17 && kind != 18) return null;
            String name = cstrOrNull(rel(d.add(8)));
            Address fd = rel(d.add(16));
            StringBuilder b = new StringBuilder("{\"address\":").append(q("0x" + d.toString()))
                .append(",\"kind\":").append(q(kind == 16 ? "class" : kind == 17 ? "struct" : "enum"))
                .append(",\"name\":").append(q(name));
            b.append(",\"fields_address\":").append(q(fd == null ? null : "0x" + fd.toString()));
            b.append(",\"fields\":[");
            if (fd != null) {
                int size = currentProgram.getMemory().getShort(fd.add(10)) & 0xffff;
                int n = currentProgram.getMemory().getInt(fd.add(12));
                if (size != 12 || n < 0 || n > 4096) {
                    b.append("],\"fields_error\":").append(q("unexpected field record layout"));
                    return b.append("}").toString();
                }
                for (int i = 0; i < n; i++) {
                    Address r = fd.add(16 + (long) i * size);
                    int fl = currentProgram.getMemory().getInt(r);
                    Address tn = rel(r.add(4));
                    Address nm = rel(r.add(8));
                    if (i > 0) b.append(",");
                    b.append("{\"name\":").append(q(cstrOrNull(nm)))
                     .append(",\"type\":").append(q(tn == null ? null : mangled(tn)))
                     .append(",\"var\":").append((fl & 2) != 0).append("}");
                }
            }
            return b.append("]}").toString();
        } catch (MemoryAccessException e) {
            return null;
        }
    }

    private static final Pattern STRING_CALL =
        Pattern.compile("builtinStringLiteral\\w*\\s*\\(\\s*(\\w+)\\s*,\\s*(0x[0-9a-fA-F]+|\\d+)");
    private static final Pattern LABEL_ADDRESS = Pattern.compile("_([0-9a-fA-F]{8})$");

    // The decompiler prints a non-ASCII string literal as a label (`s_<text>_<address>`), not as text. For each such
    // argument of Swift's string-literal builder, read the UTF-8 bytes at that address (length from the call).
    private String literals(String code) {
        StringBuilder b = new StringBuilder();
        Matcher m = STRING_CALL.matcher(code);
        int n = 0;
        while (m.find()) {
            Matcher a = LABEL_ADDRESS.matcher(m.group(1));
            if (!a.find()) continue;
            String text = null;
            try {
                long len = m.group(2).startsWith("0x") ? Long.parseLong(m.group(2).substring(2), 16)
                                                       : Long.parseLong(m.group(2));
                if (len > 0 && len <= 4096) {
                    byte[] bytes = new byte[(int) len];
                    currentProgram.getMemory().getBytes(toAddr(Long.parseLong(a.group(1), 16)), bytes);
                    text = StandardCharsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(bytes)).toString();
                }
            } catch (Exception e) {
                text = null;
            }
            if (n++ > 0) b.append(",");
            b.append(q(m.group(1))).append(":").append(q(text));
        }
        return "{" + b + "}";
    }

    // The number 0..30 of the general register an object designates (w5 and x5 are both 5), or -1 when it is not one.
    private static int regNumber(Object o) {
        if (!(o instanceof Register)) return -1;
        String n = ((Register) o).getBaseRegister().getName().toLowerCase();
        if (n.length() < 2 || n.charAt(0) != 'x') return -1;
        try {
            int v = Integer.parseInt(n.substring(1));
            return v <= 30 ? v : -1;
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    private static boolean isZeroRegister(Object o) {
        if (!(o instanceof Register)) return false;
        String r = ((Register) o).getName().toLowerCase();
        return r.equals("wzr") || r.equals("xzr");
    }

    // The abstract value a move leaves in its destination: an immediate (`mov`/`movz` constant, `orr <d>, wzr/xzr, #const`),
    // zero (`mov <d>, wzr/xzr`) or a copy of a known register (`mov <d>, <m>`, `orr <d>, wzr/xzr, <m>`). `known` and `val`
    // are the register state; the result is null when the instruction is none of these or the source is unknown.
    private static Long moved(Instruction in, boolean[] known, long[] val) {
        String m = in.getMnemonicString().toLowerCase();
        int n = in.getNumOperands();
        int srcOp = -1;
        if ((m.equals("mov") || m.equals("movz")) && n == 2) srcOp = 1;
        else if (m.equals("orr") && n == 3) {
            Object[] a = in.getOpObjects(1);
            if (a.length != 1 || !isZeroRegister(a[0])) return null;
            srcOp = 2;
        } else return null;
        Scalar sc = in.getScalar(srcOp);
        if (sc != null) return sc.getUnsignedValue();
        Object[] ops = in.getOpObjects(srcOp);
        if (ops.length != 1) return null;
        if (isZeroRegister(ops[0])) return 0L;
        int r = regNumber(ops[0]);
        if (r < 0 || !known[r]) return null;
        boolean w = ((Register) ops[0]).getBitLength() <= 32;
        return w ? (val[r] & 0xffffffffL) : val[r];
    }

    // True when the instruction reads the frame pointer (x29) or the stack pointer.
    private static boolean readsFrameOrStack(Instruction in) {
        for (Object o : in.getInputObjects()) {
            if (regNumber(o) == 29) return true;
            if (o instanceof Register && ((Register) o).getBaseRegister().getName().equalsIgnoreCase("sp")) return true;
        }
        return false;
    }

    // The frame-pointer offset of a memory operand `[x29, #imm]` of operand `op`, or null for any other addressing.
    private static Long frameOffset(Instruction in, int op) {
        Object[] ops = in.getOpObjects(op);
        if (ops.length == 0 || regNumber(ops[0]) != 29) return null;
        long off = 0;
        for (int i = 1; i < ops.length; i++) {
            if (!(ops[i] instanceof Scalar)) return null;
            off += ((Scalar) ops[i]).getSignedValue();
        }
        return off;
    }

    // For each constructor call (callee symbol ending `fC`) in a one-time initialiser: the integer registers x0..x7 that
    // hold a constant at the call, by a forward walk of the function's instructions in address order. The walk follows
    // immediate moves, register copies and stores to and loads from frame-pointer slots (`stur/str/stp/ldur/ldr/ldp
    // [x29, #imm]`). Everything it knows is dropped at an instruction another instruction jumps or branches to and after
    // an unconditional jump or a return; the registers x0..x18 and x30 are dropped after a call. The slots are dropped
    // for good once an `add`/`sub` forms the address of the frame or stack pointer (the slot may be written through it).
    // Any other write makes the register unknown. `{"address","callee","registers":{"x5":5}}` (64-bit register value).
    private String callSites(Function f) {
        StringBuilder b = new StringBuilder();
        int n = 0;
        boolean[] known = new boolean[31];
        long[] val = new long[31];
        java.util.HashMap<Long, Long> slot = new java.util.HashMap<>();
        boolean escaped = false;
        Instruction prev = null;
        for (Instruction in : currentProgram.getListing().getInstructions(f.getBody(), true)) {
            boolean reset = prev == null || !in.getAddress().equals(prev.getFallThrough());
            if (!reset) {
                for (Reference r : getReferencesTo(in.getAddress())) {
                    if (r.getReferenceType().isFlow() && !r.getReferenceType().equals(RefType.FALL_THROUGH)) reset = true;
                }
            }
            if (reset) {
                java.util.Arrays.fill(known, false);
                slot.clear();
            }
            prev = in;
            String m = in.getMnemonicString().toLowerCase();
            if (in.getFlowType().isCall()) {
                Address[] flows = in.getFlows();
                Function callee = flows != null && flows.length == 1 ? getFunctionAt(flows[0]) : null;
                String cn = callee == null ? null : (callee.getSymbol() == null ? callee.getName() : callee.getSymbol().getName());
                if (cn != null && cn.endsWith("fC") && !cn.contains("builtinStringLiteral")) {
                    StringBuilder regs = new StringBuilder();
                    int k = 0;
                    for (int r = 0; r < 8; r++) {
                        if (!known[r]) continue;
                        if (k++ > 0) regs.append(",");
                        regs.append("\"x").append(r).append("\":").append(val[r]);
                    }
                    if (n++ > 0) b.append(",");
                    b.append("{\"address\":").append(q("0x" + in.getAddress().toString())).append(",\"callee\":")
                     .append(q(cn)).append(",\"registers\":{").append(regs).append("}}");
                }
                for (int r = 0; r <= 18; r++) known[r] = false;
                known[30] = false;
                continue;
            }
            Long off = null;
            int memOp = m.startsWith("st") || m.startsWith("ld") ? in.getNumOperands() - 1 : -1;
            if (memOp >= 1) off = frameOffset(in, memOp);
            if (m.startsWith("st") && memOp >= 1) {
                boolean plain = (m.equals("str") || m.equals("stur") || m.equals("stp")) && off != null && !escaped;
                if (off != null && !plain) slot.clear();
                if (plain) {
                    int regsInOp = m.equals("stp") ? 2 : 1;
                    for (int i = 0; i < regsInOp; i++) {
                        Object[] so = in.getOpObjects(i);
                        Long v = null;
                        if (so.length == 1 && isZeroRegister(so[0])) v = 0L;
                        else if (so.length == 1 && regNumber(so[0]) >= 0 && known[regNumber(so[0])]) {
                            boolean w = ((Register) so[0]).getBitLength() <= 32;
                            v = w ? (val[regNumber(so[0])] & 0xffffffffL) : val[regNumber(so[0])];
                        }
                        boolean w = so.length == 1 && so[0] instanceof Register && ((Register) so[0]).getBitLength() <= 32;
                        long key = off + i * (w ? 4 : 8);
                        // A 32-bit store only describes a 32-bit slot: kept under a distinct key so a 64-bit load misses it.
                        slot.remove(off + i * 8L);
                        slot.remove(off + i * 8L + 4);
                        if (v != null) slot.put(w ? -(key + 1) : key, v);
                    }
                }
                continue;
            }
            Long v = null;
            if (m.startsWith("ld") && off != null && !escaped && (m.equals("ldr") || m.equals("ldur") || m.equals("ldp"))) {
                int regsInOp = m.equals("ldp") ? 2 : 1;
                for (int i = 0; i < regsInOp; i++) {
                    Object[] lo = in.getOpObjects(i);
                    if (lo.length != 1 || regNumber(lo[0]) < 0) continue;
                    boolean w = ((Register) lo[0]).getBitLength() <= 32;
                    long key = off + i * (w ? 4 : 8);
                    Long sv = slot.get(w ? -(key + 1) : key);
                    int rn = regNumber(lo[0]);
                    known[rn] = sv != null;
                    if (sv != null) val[rn] = sv;
                }
                continue;
            }
            v = moved(in, known, val);
            boolean writesOther = false;
            for (Object o : in.getResultObjects()) {
                int rn = regNumber(o);
                if (rn < 0) continue;
                if (rn != 29) writesOther = true;
                boolean w = ((Register) o).getBitLength() <= 32;
                if (v != null) {
                    known[rn] = true;
                    val[rn] = w ? (v & 0xffffffffL) : v;
                } else {
                    known[rn] = false;
                }
            }
            if ((m.equals("add") || m.equals("sub")) && writesOther && readsFrameOrStack(in)) {
                escaped = true;
                slot.clear();
            }
        }
        return "[" + b + "]";
    }

    @Override
    public void run() throws Exception {
        String out = null, include = ".*", data = null, xrefs = null;
        boolean swift = false;
        int max = Integer.MAX_VALUE, timeout = 60;
        for (String a : getScriptArgs()) {
            int i = a.indexOf('=');
            if (i < 0) continue;
            String k = a.substring(0, i), v = a.substring(i + 1);
            switch (k) {
                case "out": out = v; break;
                case "include": include = v; break;
                case "max": max = Integer.parseInt(v); break;
                case "timeout": timeout = Integer.parseInt(v); break;
                case "data": data = v; break;
                case "xrefs": xrefs = v; break;
                case "swift": swift = v.equals("1"); break;
                default: throw new IllegalArgumentException("unknown argument " + k);
            }
        }
        if (out == null) throw new IllegalArgumentException("out= is required");
        Pattern inc = Pattern.compile(include);

        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        long t0 = System.nanoTime();
        int total = 0, matched = 0, decompiled = 0, failed = 0;
        StringBuilder fns = new StringBuilder();
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        while (it.hasNext() && !monitor.isCancelled()) {
            Function f = it.next();
            if (f.isExternal() || f.isThunk()) continue;
            total++;
            String sym = f.getSymbol() == null ? f.getName() : f.getSymbol().getName();
            if (!inc.matcher(sym).find() && !inc.matcher(f.getName()).find()) continue;
            matched++;
            if (decompiled + failed >= max) continue;
            String code = null, err = null;
            DecompileResults r = ifc.decompileFunction(f, timeout, monitor);
            if (r != null && r.decompileCompleted() && r.getDecompiledFunction() != null) {
                code = r.getDecompiledFunction().getC();
                decompiled++;
            } else {
                err = r == null ? "no result" : String.valueOf(r.getErrorMessage());
                failed++;
            }
            if (fns.length() > 0) fns.append(",\n");
            fns.append("{\"address\":").append(q("0x" + f.getEntryPoint().toString()))
               .append(",\"name\":").append(q(f.getName()))
               .append(",\"symbol\":").append(q(sym))
               .append(",\"size\":").append(f.getBody().getNumAddresses())
               .append(",\"string_literals\":").append(code == null ? "{}" : literals(code))
               .append(",\"call_sites\":").append(sym.endsWith("_WZ") ? callSites(f) : "[]")
               .append(",\"pseudo_code\":").append(q(code))
               .append(",\"error\":").append(q(err)).append("}");
        }
        double decompSeconds = (System.nanoTime() - t0) / 1e9;

        StringBuilder syms = new StringBuilder();
        int dataCount = 0;
        if (data != null) {
            Pattern dp = Pattern.compile(data);
            SymbolIterator si = currentProgram.getSymbolTable().getAllSymbols(true);
            while (si.hasNext()) {
                Symbol s = si.next();
                if (!dp.matcher(s.getName()).find()) continue;
                Data d = currentProgram.getListing().getDataAt(s.getAddress());
                if (d == null) continue;
                int len = Math.min(d.getLength(), 64);
                byte[] bytes = new byte[len];
                String hex;
                try {
                    currentProgram.getMemory().getBytes(s.getAddress(), bytes);
                    StringBuilder h = new StringBuilder();
                    for (byte x : bytes) h.append(String.format("%02x", x));
                    hex = h.toString();
                } catch (MemoryAccessException e) {
                    hex = null;
                }
                if (dataCount++ > 0) syms.append(",\n");
                syms.append("{\"address\":").append(q("0x" + s.getAddress().toString()))
                    .append(",\"name\":").append(q(s.getName()))
                    .append(",\"size\":").append(d.getLength())
                    .append(",\"bytes_hex\":").append(q(hex))
                    .append(",\"value\":").append(q(d.getDefaultValueRepresentation())).append("}");
            }
        }

        StringBuilder xr = new StringBuilder();
        int xrCount = 0;
        if (xrefs != null) {
            try (BufferedReader br = new BufferedReader(new FileReader(xrefs, StandardCharsets.UTF_8))) {
                String line;
                while ((line = br.readLine()) != null) {
                    line = line.trim();
                    if (line.isEmpty()) continue;
                    Address ad = toAddr(line);
                    Set<String> from = new LinkedHashSet<>();
                    if (ad != null) {
                        for (Reference ref : getReferencesTo(ad)) {
                            Function f = getFunctionContaining(ref.getFromAddress());
                            if (f != null) from.add(f.getEntryPoint().toString() + "|" + f.getName());
                        }
                    }
                    if (xrCount++ > 0) xr.append(",\n");
                    xr.append("{\"address\":").append(q(line)).append(",\"functions\":[");
                    int n = 0;
                    for (String s : from) {
                        String[] p = s.split("\\|", 2);
                        if (n++ > 0) xr.append(",");
                        xr.append("{\"address\":").append(q("0x" + p[0])).append(",\"name\":").append(q(p[1])).append("}");
                    }
                    xr.append("]}");
                }
            }
        }

        StringBuilder sw = new StringBuilder();
        int swCount = 0;
        String swNote = "not requested";
        if (swift) {
            MemoryBlock blk = currentProgram.getMemory().getBlock("__swift5_types");
            if (blk == null) {
                swNote = "no __swift5_types section";
            } else {
                swNote = "ok";
                for (long off = 0; off + 4 <= blk.getSize(); off += 4) {
                    Address slot = blk.getStart().add(off);
                    String rec = swiftType(slot);
                    if (rec == null) continue;
                    if (swCount++ > 0) sw.append(",\n");
                    sw.append(rec);
                }
            }
        }

        StringBuilder doc = new StringBuilder("{\"format\":1");
        doc.append(",\"program\":").append(q(currentProgram.getName()));
        doc.append(",\"program_id\":").append(q(String.valueOf(currentProgram.getDomainFile().getFileID())));
        doc.append(",\"ghidra_version\":").append(q(getGhidraVersion()));
        doc.append(",\"functions_total\":").append(total);
        doc.append(",\"functions_matched\":").append(matched);
        doc.append(",\"decompiled\":").append(decompiled);
        doc.append(",\"decompile_failed\":").append(failed);
        doc.append(",\"decompile_seconds\":").append(String.format(java.util.Locale.ROOT, "%.1f", decompSeconds));
        doc.append(",\"functions\":[\n").append(fns).append("]");
        doc.append(",\"data_symbols\":[\n").append(syms).append("]");
        doc.append(",\"swift_types_status\":").append(q(swNote));
        doc.append(",\"swift_types\":[\n").append(sw).append("]");
        doc.append(",\"xrefs\":[\n").append(xr).append("]}\n");
        Files.write(Paths.get(out), doc.toString().getBytes(StandardCharsets.UTF_8));
        println("BlueprintExport: " + decompiled + " decompiled, " + failed + " failed, " + matched + " matched of " + total);
    }
}
