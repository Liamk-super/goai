const DATABASE_NAME = "launchscope-pending-intake-files-v1";
const STORE_NAME = "transfers";
const DATABASE_VERSION = 1;
const TRANSFER_TTL_MS = 30 * 60 * 1000;

type StoredFile = {
  name: string;
  type: string;
  lastModified: number;
  payload: Blob;
};

type StoredTransfer = {
  transferId: string;
  expiresAt: number;
  files: StoredFile[];
};

function fileIdentity(file: Pick<File, "name" | "size" | "type" | "lastModified">): string {
  return `${file.name}\u0000${file.size}\u0000${file.type}\u0000${file.lastModified}`;
}

export function mergePendingIntakeFiles(current: File[], selected: File[]): File[] {
  const identities = new Set(current.map(fileIdentity));
  const additions = selected.filter(file => {
    const identity = fileIdentity(file);
    if (identities.has(identity)) return false;
    identities.add(identity);
    return true;
  });
  return [...current, ...additions];
}

function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new Error("This browser cannot preserve selected files across navigation."));
  }
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME, { keyPath: "transferId" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("The pending material store could not be opened."));
  });
}

function transactionCompleted(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error ?? new Error("The pending material transaction failed."));
    transaction.onabort = () => reject(transaction.error ?? new Error("The pending material transaction was aborted."));
  });
}

function deleteExpiredTransfers(store: IDBObjectStore, now: number) {
  const request = store.openCursor();
  request.onsuccess = () => {
    const cursor = request.result;
    if (!cursor) return;
    const transfer = cursor.value as StoredTransfer;
    if (transfer.expiresAt <= now) cursor.delete();
    cursor.continue();
  };
}

export async function stagePendingIntakeFiles(transferId: string, files: File[]): Promise<void> {
  if (!files.length) return;
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    deleteExpiredTransfers(store, Date.now());
    store.put({
      transferId,
      expiresAt: Date.now() + TRANSFER_TTL_MS,
      files: files.map(file => ({
        name: file.name,
        type: file.type,
        lastModified: file.lastModified,
        payload: file.slice(0, file.size, file.type),
      })),
    } satisfies StoredTransfer);
    await transactionCompleted(transaction);
  } finally {
    database.close();
  }
}

export async function takePendingIntakeFiles(transferId: string): Promise<File[]> {
  if (!transferId) return [];
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const store = transaction.objectStore(STORE_NAME);
    const completed = transactionCompleted(transaction);
    const transfer = await new Promise<StoredTransfer | undefined>((resolve, reject) => {
      const request = store.get(transferId);
      request.onsuccess = () => {
        store.delete(transferId);
        resolve(request.result as StoredTransfer | undefined);
      };
      request.onerror = () => reject(request.error ?? new Error("The selected materials could not be restored."));
    });
    await completed;
    if (!transfer || transfer.expiresAt <= Date.now()) return [];
    return transfer.files.map(file => new File([file.payload], file.name, {
      type: file.type,
      lastModified: file.lastModified,
    }));
  } finally {
    database.close();
  }
}

export async function discardPendingIntakeFiles(transferId: string): Promise<void> {
  if (!transferId) return;
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).delete(transferId);
    await transactionCompleted(transaction);
  } finally {
    database.close();
  }
}
