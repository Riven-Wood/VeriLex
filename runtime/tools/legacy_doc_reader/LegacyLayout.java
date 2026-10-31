// VeriLex metadata bridge. Apache POI handles the binary Word format.
// Compiled against pinned POI 6.0.0. This package grants access to the already
// decoded complex-script size; no private binary-file parser is implemented here.
package org.apache.poi.hwpf.usermodel;

import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import org.apache.poi.hwpf.HWPFDocument;

public final class LegacyLayout {
    private static String fingerprint(String text) throws Exception {
        var normalized = new StringBuilder();
        Range.stripFields(text).codePoints().filter(c -> !Character.isWhitespace(c)
            && !Character.isSpaceChar(c) && !Character.isISOControl(c))
            .forEach(normalized::appendCodePoint);
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
            .digest(normalized.toString().getBytes(StandardCharsets.UTF_8)));
    }

    public static void main(String[] args) throws Exception {
        System.setProperty("log4j2.loggerContextFactory", "org.apache.logging.log4j.simple.SimpleLoggerContextFactory");
        if (args.length != 1) throw new IllegalArgumentException("one local document required");
        try (var input = new FileInputStream(args[0]); var doc = new HWPFDocument(input)) {
            var range = doc.getRange();
            if (range.numParagraphs() > 100000) throw new IllegalArgumentException("paragraph limit");
            byte[] typography = doc.getDocProperties().getDoptypography();
            int flags = Byte.toUnsignedInt(typography[0]);
            // MS-DOC 2.7.16: fKerningPunct bit 0, iJustification bits 1..2.
            int compression = (flags >> 1) & 3;
            if (compression > 2) throw new IllegalArgumentException("invalid compression");
            var result = new StringBuilder("{\"version\":1,\"compression\":")
                .append(compression).append(",\"kernPunctuation\":")
                .append((flags & 1) != 0).append(",\"paragraphs\":[");
            int written = 0;
            for (int i = 0; i < range.numParagraphs(); i++) {
                var p = range.getParagraph(i);
                // Binary Word represents each table row terminator as a
                // paragraph. OOXML has a structural </w:tr> instead.
                if (p.isTableRowEnd()) {
                    if (!p.text().codePoints().allMatch(Character::isISOControl))
                        throw new IllegalArgumentException("table row marker contains text");
                    continue;
                }
                if (written++ > 0) result.append(',');
                result.append("{\"hash\":\"").append(fingerprint(p.text()))
                    .append("\",\"table\":").append(p.isInTable());
                // Only a genuinely empty body paragraph is repaired. Whitespace,
                // fields, tables and deleted paragraph marks are not inferred blank.
                if (p.text().equals("\r") && !p.isInTable() && p.numCharacterRuns() == 1) {
                    var run = p.getCharacterRun(0);
                    if (!run.isMarkedDeleted()) {
                        result.append(",\"emptySize\":").append(run.getFontSize())
                            .append(",\"emptySizeCs\":").append(run._props.getHpsBi());
                    }
                }
                result.append('}');
            }
            System.out.println(result.append("]}"));
        }
    }
}
