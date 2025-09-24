import { Database } from "@nozbe/watermelondb";
import LokiJSAdapter from "@nozbe/watermelondb/adapters/lokijs";
import Chat from "./Chat";
import Message from "./Message";
import { migrations } from "./migrations";
import { mySchema } from "./schema";

const adapter = new LokiJSAdapter({
  schema: mySchema,
  migrations,
  useWebWorker: false,
  useIncrementalIndexedDB: true,
  onSetUpError: (error) => {
    console.error("Failed to set up database:", error);
  },
});

export const database = new Database({
  adapter,
  modelClasses: [Chat, Message],
});
