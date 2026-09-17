import com.adobe.epubcheck.tool.EpubChecker;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;

/**
 * Keeps one JVM warm so EPUBCheck's multi-second schema initialisation happens once,
 * not once per EPUB. Protocol, one request per line on stdin:
 *
 *     <epub path>\t<json report path>
 *
 * and one reply per line on stdout: EPUBCheck's exit code (0 = valid, 1 = errors found,
 * anything else = it could not run). Exits when stdin closes.
 *
 * Compiled with: javac -cp epubcheck.jar EpubCheckServer.java   (any JDK 11+)
 */
public final class EpubCheckServer {
    public static void main(String[] args) throws Exception {
        PrintStream out = new PrintStream(System.out, true, "UTF-8");
        System.setOut(new PrintStream(System.err, true, "UTF-8")); // EPUBCheck chatter -> stderr
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        out.println("ready");
        String line;
        while ((line = in.readLine()) != null) {
            int tab = line.indexOf('\t');
            if (tab < 0) {
                out.println("2");
                continue;
            }
            String epub = line.substring(0, tab);
            String report = line.substring(tab + 1);
            int code;
            try {
                code = new EpubChecker().run(new String[] {epub, "--json", report, "--quiet"});
            } catch (Throwable t) {
                t.printStackTrace();
                code = 2;
            }
            out.println(Integer.toString(code));
        }
    }
}
