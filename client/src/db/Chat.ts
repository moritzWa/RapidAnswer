import { Model } from "@nozbe/watermelondb";
import { children, date, readonly } from "@nozbe/watermelondb/decorators";

export default class Chat extends Model {
  static table = "chats";

  static associations = {
    messages: { type: "has_many", foreignKey: "chat_id" },
  } as const;

  @readonly @date("created_at") createdAt!: Date;
  @readonly @date("updated_at") updatedAt!: Date;

  @children("messages") messages!: any;
}
