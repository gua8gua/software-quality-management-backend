import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import edu.kit.kastel.sdq.lissa.ratlr.Configuration;
import edu.kit.kastel.sdq.lissa.ratlr.elementstore.ElementStore;
import edu.kit.kastel.sdq.lissa.ratlr.knowledge.Element;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;

/** JSON stdin/stdout boundary; retrieval itself is the unmodified upstream implementation. */
public final class LissaRetrievalBridge {
    public static void main(String[] args) throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        JsonNode input = mapper.readTree(System.in);
        int topK = input.get("top_k").asInt();
        var config = new Configuration.ModuleConfiguration("custom", Map.of("max_results", "" + topK));
        ElementStore store = new ElementStore(config, true);
        var elements = new ArrayList<Element>();
        var embeddings = new ArrayList<float[]>();
        for (JsonNode target : input.get("targets")) {
            elements.add(new Element(target.get("id").asText(), "artifact", "", 0, (Element) null, true));
            embeddings.add(mapper.convertValue(target.get("vector"), float[].class));
        }
        store.setup(elements, embeddings);
        var result = new ArrayList<Map<String, Object>>();
        for (JsonNode source : input.get("sources")) {
            var matches = store.findSimilarWithDistances(mapper.convertValue(source.get("vector"), float[].class));
            int rank = 0;
            for (var match : matches) {
                var row = new LinkedHashMap<String, Object>();
                row.put("source_id", source.get("id").asText());
                row.put("target_id", match.first().getIdentifier());
                row.put("rank", ++rank);
                row.put("similarity", match.second());
                result.add(row);
            }
        }
        mapper.writeValue(System.out, result);
    }
}
