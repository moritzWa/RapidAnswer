import { Database } from "@nozbe/watermelondb";
import { createContext, useContext, type ReactNode } from "react";
import { database } from "../db";

const DatabaseContext = createContext<Database | null>(null);

export const DatabaseProvider = ({ children }: { children: ReactNode }) => (
  <DatabaseContext.Provider value={database}>
    {children}
  </DatabaseContext.Provider>
);

export const useDatabase = () => {
  const dbInstance = useContext(DatabaseContext);
  if (!dbInstance) {
    throw new Error("useDatabase must be used within a DatabaseProvider");
  }
  return dbInstance;
};
