import { Model } from "@nozbe/watermelondb";
import {
  field,
  readonly,
  date,
  relation,
} from "@nozbe/watermelondb/decorators";
import Chat from "./Chat";

export default class Message extends Model {
  static table = "messages";

  static associations = {
    chats: { type: "belongs_to", key: "chat_id" },
  } as const;

  @field("body") body!: string;
  @field("is_user") isUser!: boolean;
  @readonly @date("created_at") createdAt!: Date;
  @readonly @date("updated_at") updatedAt!: Date;

  @relation("chats", "chat_id") chat!: any;
}
