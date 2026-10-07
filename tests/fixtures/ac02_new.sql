-- schema novo
CREATE TABLE posts (
  user_id INT,
  id INT PRIMARY KEY
);
CREATE TABLE users (
  age INT,           /* idade */
  id INT NOT NULL PRIMARY KEY,
  email VARCHAR(100) NOT NULL
);
