class octree:
    def __init__(self, depth, cube):
        if cube == None:
            self.depth = depth
            self.data = [[0] * 8**_ for _ in range(depth)]
        else:
            grid_size = cude.shape[-1]
            self.depth = np.log2(grid_size)
            self.data = [[0] * 8**_ for _ in range(self.depth)]
            for z in range(self.depth):
                for y in range(self.depth):
                    for x in range(self.depth):
                        self.data[-1][z* grid_size**2 + y * grid_size + x] = cube[z,y,x]

    def get(self, (x, y, z), grid_size):
        return self.data[-1][z* grid_size**2 + y * grid_size + x]

    def divide(self):
        self.depth += 1
        new_level = []
        for i in self.data[-1]:
            new_level.extend([i] * 8)
        self.data.append(new_level)
